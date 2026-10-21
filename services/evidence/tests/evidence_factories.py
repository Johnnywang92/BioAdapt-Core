"""Wiring for the evidence-service tests.

These tests sit below the workflow layer on purpose (section 9.5): the products below are typed out as
the JSON a reviewer would open, not produced by a workflow. That is what makes them worth having - a card
that only builds against the reference implementation's own bytes has not proved that the reader follows
the contract, and the root integration test is where the real bytes are read.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Mapping, Optional, Sequence

from bioadapt_schemas.entities import AssetLineage, CandidateSet, DataAsset, Project, RunManifest
from bioadapt_schemas.enums import (
    AssetRole,
    CandidateType,
    FilteringState,
    PrivacyTier,
    ReproducibilityLevel,
    RetentionClass,
    ToolClass,
    UsageScope,
)
from bioadapt_schemas.identifiers import (
    CELL_KEY_SEPARATOR,
    candidate_id,
    canonical_cell_key,
    index_hash,
    member_fingerprint,
    sha256_hex,
)
from bioadapt_schemas.plan import (
    MANDATORY_STOP_CONDITIONS,
    AnalysisPlan,
    ConfoundingCheck,
    DataPartitions,
    DesignMatrix,
    LoopBudget,
    NodeResources,
    PlanNode,
)
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS
from bioadapt_storage.catalog import Catalog
from bioadapt_storage.objectstore import InMemoryObjectStore, object_key

UTC = timezone.utc
NOW = datetime(2026, 6, 1, 9, 0, tzinfo=UTC)
TODAY = NOW.date()

PROJECT_ID = "prj_evidence"
LOCAL_PROJECT_ID = "prj_evidence_local"
RUN_ID = "run_evidence_01"
PLAN_ID = "plan_evidence"
PLAN_VERSION = "1"

PI = "user_pi_01"
ANALYST = "user_analyst_01"
COMPLIANCE = "user_compliance_01"

DISCOVERY_SPECIMENS = ["spc_donor_a", "spc_donor_b"]
CONFIRMATION_SPECIMENS = ["spc_donor_c"]

#: The nodes a card cites tools from. ``assets.node_tool_of`` resolves a produced asset through the run's
#: pinned plan, so a test that wants a wrong attribution edits this table, not the reader.
NODE_TOOLS: dict[str, tuple[str, str]] = {
    "qc": ("scrnaseq_qc", "1.0.0"),
    "clustering": ("scrnaseq_cluster", "1.0.0"),
    "markers": ("scrnaseq_markers", "1.0.0"),
    "pseudobulk_de": ("scrnaseq_pseudobulk_de", "1.0.0"),
    "robustness": ("scrnaseq_robustness", "1.0.0"),
    "coverage": ("scrnaseq_coverage", "1.0.0"),
    "state_axis": ("scrnaseq_state_axis", "1.0.0"),
}

LOCAL_ENDPOINT = "https://model.internal"
CLOUD_ENDPOINT = "https://models.example.com"


def clock() -> datetime:
    return NOW


def cell_key(specimen: str, barcode: str) -> str:
    return canonical_cell_key(specimen, barcode)


def node(node_id: str, **overrides: Any) -> PlanNode:
    tool_name, tool_version = NODE_TOOLS[node_id]
    fields: dict[str, Any] = {
        "node_id": node_id,
        "tool_name": tool_name,
        "tool_version": tool_version,
        "tool_class": ToolClass.REGISTERED,
        "outputs": [node_id],
        "resources": NodeResources(cpu=4, memory_gb=16, max_minutes=60),
        "privacy_policy": "project_local",
    }
    fields.update(overrides)
    return PlanNode(**fields)


def plan(*, plan_version: str = PLAN_VERSION, max_token_cost_usd: float = 5.0, **overrides: Any) -> AnalysisPlan:
    fields: dict[str, Any] = {
        "plan_id": PLAN_ID,
        "plan_version": plan_version,
        "project_id": PROJECT_ID,
        "objective": "resolve_an_uncharacterized_population",
        "design_matrix": DesignMatrix(
            formula="~ group",
            group_variable="group",
            min_n_per_group=3,
            confounding_checks=[ConfoundingCheck(name="group_vs_donor", tool="design_check", status="PASS")],
        ),
        "data_partitions": DataPartitions(
            rule="holdout_donors_v1",
            discovery_specimens=list(DISCOVERY_SPECIMENS),
            confirmation_specimens=list(CONFIRMATION_SPECIMENS),
        ),
        "qc_policy_id": "qc_evidence_reference",
        "loop_budget": LoopBudget(max_compute_minutes=180, max_token_cost_usd=max_token_cost_usd),
        "root_inputs": ["counts"],
        "nodes": [node(node_id) for node_id in NODE_TOOLS],
        "stop_conditions": list(MANDATORY_STOP_CONDITIONS),
        "provenance_policy": "run_manifest_v1",
    }
    fields.update(overrides)
    return AnalysisPlan(**fields)


def project(project_id: str = PROJECT_ID, **overrides: Any) -> Project:
    fields: dict[str, Any] = {
        "project_id": project_id,
        "study_goal": "resolve an uncharacterized immune population",
        "species": "human",
        "privacy_tier": PrivacyTier.RESTRICTED,
        "created_by": PI,
        "annotation_granularity_target": "cell_type_fine",
        "review_load_budget_hours_per_week": 6.0,
        "created_at": NOW,
    }
    fields.update(overrides)
    return Project(**fields)


def manifest(run_id: str = RUN_ID, **overrides: Any) -> RunManifest:
    fields: dict[str, Any] = {
        "run_id": run_id,
        "project_id": PROJECT_ID,
        "plan_id": PLAN_ID,
        "plan_version": PLAN_VERSION,
        "tool_versions": {name: version for name, version in NODE_TOOLS.values()},
        "tool_registry_snapshot": "snap_evidence_01",
        "input_hashes": {},
        "reproducibility_level_target": ReproducibilityLevel.BYTE,
        "started_at": NOW,
    }
    fields.update(overrides)
    return RunManifest(**fields)


def catalog_with_run(
    objects: InMemoryObjectStore,
    *,
    project_id: str = PROJECT_ID,
    tier: PrivacyTier = PrivacyTier.RESTRICTED,
    the_plan: Optional[AnalysisPlan] = None,
    run_id: str = RUN_ID,
) -> Catalog:
    """An index with one project, one pinned plan version and one run that names its root counts.

    A manifest has to name at least one input, and an upload is indexed without a producing run (section
    5.2), so the fixture writes the root product the way an upload would - which is also the case the
    reader has to resolve through the manifest rather than through the run's own outputs.
    """
    made = Catalog(":memory:", clock=clock, check_same_thread=False)
    made.create_project(project(project_id, privacy_tier=tier))
    made.set_scope(project_id)
    made.record_plan(the_plan or plan(), created_by=ANALYST)
    root = write_product(
        made,
        objects,
        slot="counts",
        schema_version="expression_v1",
        payload={"matrix": {"shape": [4, 3]}, "transform": "counts", "parameters": {"staged_by": "upload"}},
        node_id="qc",
        run_id="",
        retention_class=RetentionClass.DELIVERABLE,
    )
    made.record_run(
        manifest(run_id, project_id=project_id, input_hashes={root: made.get_asset(root).file_sha256}),
        state="ANALYSIS_RUNNING",
    )
    return made


def product_document(slot: str, schema_version: str, payload: Mapping[str, Any]) -> bytes:
    """The stored form: the data, plus which slot it answers for and which contract it follows."""
    return json.dumps({"slot": slot, "schema_version": schema_version, **payload}, sort_keys=True).encode()


def write_product(
    catalog: Catalog,
    objects: InMemoryObjectStore,
    *,
    slot: str,
    schema_version: str,
    payload: Mapping[str, Any],
    node_id: str,
    run_id: str = RUN_ID,
    cell_keys: Sequence[str] = (),
    retention_class: RetentionClass = RetentionClass.RUN_LINKED,
) -> str:
    """Store one product and index it the way a finished node does: bytes first, then the row, then lineage."""
    raw = product_document(slot, schema_version, payload)
    digest = sha256_hex(raw)
    asset_id = "as_" + digest[:12]
    key = object_key(
        project_id=catalog.scope,
        asset_id=asset_id,
        file_sha256=digest,
        retention_class=retention_class,
        extension="json",
    )
    objects.put(key, raw)
    catalog.record_asset(
        DataAsset(
            asset_id=asset_id,
            project_id=catalog.scope,
            uri="s3://" + key,
            file_sha256=digest,
            content_fingerprint=index_hash(cell_keys) if cell_keys else digest,
            format="json",
            schema_version=schema_version,
            size_bytes=len(raw),
            privacy_tier=PrivacyTier.RESTRICTED,
            usage_scope=UsageScope.THIS_PROJECT_ONLY,
            retention_class=retention_class,
            filtering_state=FilteringState.NONE,
            created_at=NOW,
            created_by_run_id=run_id or None,
        )
    )
    if run_id:
        catalog.record_lineage(
            AssetLineage(run_id=run_id, node_id=node_id, asset_id=asset_id, role=AssetRole.OUTPUT, ordinal=0)
        )
    return asset_id


# --------------------------------------------------------------------- reference products


def clusters_payload(labels: Mapping[str, str], **overrides: Any) -> dict[str, Any]:
    """``{cell_key: cluster}``, in the shape the clustering node writes."""
    rows = [{"cell_key": key, "cluster": name} for key, name in sorted(labels.items())]
    fields: dict[str, Any] = {
        "method": "leiden",
        "resolution": 1.0,
        "seed": 0,
        "modularity": 0.61,
        "graph_digest": "ab" * 32,
        "singleton_share": 0.0,
        "n_clusters": len({name for _key, name in labels.items()}),
        "labels": rows,
    }
    fields.update(overrides)
    return fields


def qc_payload(cells: Mapping[str, Sequence[str]], **overrides: Any) -> dict[str, Any]:
    """One QC row per cell with the flags it carries, plus the rules that produced them."""
    fields: dict[str, Any] = {
        "policy_id": "qc_evidence_reference",
        "policy_version": "1",
        "thresholds_version": DEFAULT_THRESHOLDS.version,
        "actions": ["flag", "exclude"],
        "rules": [
            {
                "name": "mito_fraction",
                "metric": "mitochondrial_fraction",
                "action": "flag",
                "bound": 0.2,
                "threshold_source": "rule=project_qc_note:qc_pbmc_reference",
                "source_ref": "qc_pbmc_reference",
                "flagged": sum(1 for flags in cells.values() if "mito_fraction" in flags),
            },
            {
                "name": "doublet_score",
                "metric": "doublet_score",
                "action": "flag",
                "bound": 0.5,
                "threshold_source": "rule=registry_default",
                "source_ref": None,
                "flagged": sum(1 for flags in cells.values() if "doublet_score" in flags),
            },
        ],
        "cells": [{"cell_key": key, "flags": list(flags)} for key, flags in sorted(cells.items())],
        "dropped": [],
        "sensitivity": {},
    }
    fields.update(overrides)
    return fields


# --------------------------------------------------------------------- the rest of a run's products


#: Every section 7.5 perturbation class, spelled the way the robustness node records it in its arm rows.
ALL_PERTURBATIONS: tuple[str, ...] = ("resolution", "seed", "feature_subset", "leave_one_donor", "subsampling")


def labels_block(cluster: str, size: int, *, specimens: Sequence[str] = tuple(DISCOVERY_SPECIMENS)) -> dict[str, str]:
    """``size`` cells of one cluster, spread round-robin over the specimens a multi-donor run has.

    The barcode carries the cluster name so two clusters can never share a cell key: the partition built
    from these blocks is then the same set of cells the candidate fingerprints below are derived from.
    """
    chosen = list(specimens)
    return {cell_key(chosen[index % len(chosen)], f"{cluster}{index:06d}"): cluster for index in range(size)}


def partition_labels(*blocks: Mapping[str, str]) -> dict[str, str]:
    merged: dict[str, str] = {}
    for block in blocks:
        merged.update(block)
    return merged


def members_of(labels: Mapping[str, str], cluster: str) -> list[str]:
    return sorted(key for key, name in labels.items() if name == cluster)


def candidate_from_cells(
    cells: Sequence[str],
    *,
    display_name: str,
    run_id: str = RUN_ID,
    size: Optional[int] = None,
    donor_count: Optional[int] = None,
    candidate_types: Sequence[CandidateType] = (CandidateType.C3_RARE_STABLE,),
    cluster_origin: Optional[Mapping[str, Any]] = None,
) -> CandidateSet:
    """A candidate set built from a membership a test names, so a disagreeing fixture can be written out."""
    keys = sorted(set(cells))
    fingerprint = member_fingerprint(keys)
    return CandidateSet(
        candidate_id=candidate_id(fingerprint, run_id),
        run_id=run_id,
        member_fingerprint=fingerprint,
        size=len(keys) if size is None else size,
        donor_count=(
            len({key.partition(CELL_KEY_SEPARATOR)[0] for key in keys}) if donor_count is None else donor_count
        ),
        display_name=display_name,
        cluster_origin=dict(cluster_origin or {"method": "leiden", "resolution": 1.0}),
        candidate_types=list(candidate_types),
    )


def candidate_for(
    labels: Mapping[str, str],
    cluster: str,
    *,
    candidate_types: Sequence[CandidateType] = (CandidateType.C3_RARE_STABLE,),
    run_id: str = RUN_ID,
) -> CandidateSet:
    """A candidate set as the discovery step files it: identity derived from the stored membership."""
    cells = members_of(labels, cluster)
    if not cells:
        raise AssertionError(f"the fixture partition holds no cell labelled {cluster!r}")
    return candidate_from_cells(
        cells, display_name=cluster, run_id=run_id, candidate_types=candidate_types
    )


def markers_payload(clusters: Sequence[str], *, genes: Sequence[str] = ("IL7R", "CXCL13", "PDCD1"), **overrides: Any) -> dict[str, Any]:
    """The rank ordering, which section 7.8 item 5 keeps as a reading aid rather than as identity evidence."""
    fields: dict[str, Any] = {
        "method": "rank_genes_groups_wilcoxon",
        "top_n": len(genes),
        "rank_only": True,
        "cluster_digest": "cd" * 32,
        "markers": [
            {
                "cluster": cluster,
                "gene": gene,
                "rank": rank,
                "mean_in": 1.8,
                "mean_out": 0.2,
                "detection_in": 0.82,
                "detection_out": 0.06,
                "log2fc": 2.1,
            }
            for cluster in clusters
            for rank, gene in enumerate(genes, start=1)
        ],
    }
    fields.update(overrides)
    return fields


def de_table(group_a: str, group_b: str) -> list[dict[str, Any]]:
    """One gene meeting all five section 7.8 criteria, one missing donor consistency, one missing both
    the effect size and the significance cut - the shape a real table has."""
    return [
        {
            "gene": "IL7R",
            "group_a": group_a,
            "group_b": group_b,
            "log2fc": 2.14,
            "p_value": 1e-6,
            "padj": 3e-5,
            "detection_a": 0.91,
            "detection_b": 0.02,
            "donors_tested": 4,
            "donors_same_direction": 4,
        },
        {
            "gene": "CXCL13",
            "group_a": group_a,
            "group_b": group_b,
            "log2fc": 1.42,
            "p_value": 2e-4,
            "padj": 9e-4,
            "detection_a": 0.71,
            "detection_b": 0.05,
            "donors_tested": 4,
            "donors_same_direction": 3,
        },
        {
            "gene": "MS4A1",
            "group_a": group_a,
            "group_b": group_b,
            "log2fc": 0.31,
            "p_value": 0.19,
            "padj": 0.34,
            "detection_a": 0.44,
            "detection_b": 0.31,
            "donors_tested": 4,
            "donors_same_direction": 2,
        },
    ]


def weak_de_table(group_a: str, group_b: str) -> list[dict[str, Any]]:
    """A contrast where no gene survives section 7.8: the population is not marked by this table."""
    return [
        {
            "gene": "MS4A1",
            "group_a": group_a,
            "group_b": group_b,
            "log2fc": 0.31,
            "p_value": 0.19,
            "padj": 0.34,
            "detection_a": 0.44,
            "detection_b": 0.31,
            "donors_tested": 4,
            "donors_same_direction": 2,
        }
    ]


def cluster_contrast(
    name: str,
    group_a: str,
    group_b: str,
    *,
    design: str = "paired",
    donors: int = 4,
    scope: Optional[str] = None,
) -> dict[str, Any]:
    """One entry for ``contrast_details``: a population-vs-population arm pair with its design recorded."""
    return {
        name: {
            "arms": "cluster",
            "group_a": group_a,
            "group_b": group_b,
            "cluster": group_a if scope is None else scope,
            "design": design,
            "donors_a": donors,
            "donors_b": donors,
            "paired_donors": donors,
        }
    }


def condition_contrast(
    name: str = "stim_vs_ctrl",
    *,
    group_a: str = "stimulated",
    group_b: str = "control",
    scope: str = "all_cells",
    design: str = "paired",
    donors: int = 4,
) -> dict[str, Any]:
    """A condition contrast: section 7.2b reads it as a reason to look, never as an identity claim."""
    return {
        name: {
            "arms": "condition",
            "group_a": group_a,
            "group_b": group_b,
            "cluster": scope,
            "design": design,
            "donors_a": donors,
            "donors_b": donors,
            "paired_donors": donors,
        }
    }


def de_payload(
    details: Mapping[str, Mapping[str, Any]],
    tables: Sequence[Sequence[Mapping[str, Any]]],
    *,
    multiple_testing: str = "fdr_bh",
    donors_per_group: Optional[Mapping[str, int]] = None,
    **overrides: Any,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "method": "limit_donor_pseudobulk",
        "multiple_testing": multiple_testing,
        "thresholds_version": DEFAULT_THRESHOLDS.version,
        "permutation_replicates": 0,
        "seed": 0,
        "unit": "donor",
        "contrasts": [[str(d["group_a"]), str(d["group_b"])] for d in details.values()],
        "contrast_details": {name: dict(sorted(d.items())) for name, d in sorted(details.items())},
        "donors_per_group": dict(
            sorted(
                (
                    donors_per_group
                    if donors_per_group is not None
                    else {g: 4 for d in details.values() for g in (str(d["group_a"]), str(d["group_b"]))}
                ).items()
            )
        ),
        "table": [dict(row) for table in tables for row in table],
    }
    fields.update(overrides)
    return fields


def stability_payload(
    focus: str,
    *,
    kinds: Sequence[str] = ALL_PERTURBATIONS,
    baseline_cells: int = 60,
    criteria: Optional[Mapping[str, float]] = None,
    numbers: Optional[Mapping[str, Mapping[str, Any]]] = None,
    omitted_arms: Sequence[str] = (),
    deviation: Optional[str] = None,
    **overrides: Any,
) -> dict[str, Any]:
    """One arm per perturbation class, holding only agreement figures - the judgement is derived by the reader.

    ``numbers`` overrides a class's arm rows, which is how a test makes one perturbation fail without
    editing the criteria the step recorded beside it.
    """
    arms: list[dict[str, Any]] = [
        {
            "kind": kind,
            "arm": f"{kind}-1",
            "baseline_cluster": focus,
            "baseline_cells": baseline_cells,
            "cells_compared": baseline_cells,
            "cc_ari": 0.86,
            "jaccard": 0.78,
            "permutation_jaccard": 0.12,
            "marker_overlap": 0.7,
            "matched_cluster": focus,
            "matched_cells": baseline_cells,
            **(numbers or {}).get(kind, {}),
        }
        for kind in kinds
    ]
    fields: dict[str, Any] = {
        "protocol_id": "robust_pbmc_v1",
        "protocol_version": "1",
        "thresholds_version": DEFAULT_THRESHOLDS.version,
        "baseline_clusters_digest": "bc" * 32,
        "baseline_expression_digest": "be" * 32,
        "criteria": dict(
            criteria
            if criteria is not None
            else {"cc_ari_min": 0.6, "jaccard_min": 0.5, "small_cluster_max_cells": 100}
        ),
        "covered_kinds": list(kinds),
        "omitted_arms": list(omitted_arms),
        "deviation": deviation,
        "permutation_replicates": 200,
        "seed": 7,
        "baseline_sizes": {focus: baseline_cells},
        "arms": arms,
    }
    fields.update(overrides)
    return fields


def coverage_payload(
    cells: Sequence[str],
    *,
    distance: float = 1.05,
    calibration: float = 0.31,
    percentile: float = 1.0,
    criteria: Optional[Mapping[str, float]] = None,
    nearest: str = "pbmc_atlas:CD14_positive",
    **overrides: Any,
) -> dict[str, Any]:
    """Distances to the reference for the named cells, plus the cuts the step measured them against."""
    fields: dict[str, Any] = {
        "method": "knn_reference_distance",
        "embedding_digest": "em" * 32,
        "reference_digest": "rf" * 32,
        "criteria": dict(criteria if criteria is not None else {"knn": 20, "distance_ratio_min": 2.0}),
        "thresholds_version": DEFAULT_THRESHOLDS.version,
        "reference_cells": 40000,
        "calibration_cells": 5000,
        "calibration_quantiles": {"0.5": calibration, "0.9": round(calibration * 1.4, 6)},
        "candidate_quantiles": {"0.5": distance, "0.9": round(distance * 1.3, 6)},
        "median_candidate_distance": distance,
        "median_calibration_distance": calibration,
        "rows": [
            {
                "cell_key": key,
                "distance": distance,
                "calibration_percentile": percentile,
                "nearest_reference": nearest,
            }
            for key in cells
        ],
    }
    fields.update(overrides)
    return fields


def state_axis_payload(
    cells: Sequence[str],
    *,
    tail: bool = True,
    tail_rows: Optional[int] = None,
    axis_explained: float = 0.86,
    criteria: Optional[Mapping[str, float]] = None,
    extra: Sequence[str] = (),
    **overrides: Any,
) -> dict[str, Any]:
    """The declared gradient and where each cell of the population sits on it.

    ``extra`` adds cells of another population to the measurement, which is the case the reader must
    notice rather than inherit: the cosine then belongs to the pooled set, not to this candidate (§7.6).
    """
    rows = [
        {
            "cell_key": key,
            "projection": 3.2,
            "z": 2.7,
            "beyond_reference_tail": (index < tail_rows) if tail_rows is not None else tail,
        }
        for index, key in enumerate(cells)
    ]
    rows += [{"cell_key": key, "projection": 0.4, "z": 0.3, "beyond_reference_tail": False} for key in extra]
    fields: dict[str, Any] = {
        "method": "marker_gradient_projection",
        "axis": {"IFITM3": 0.62, "ISG15": 0.55, "MX1": 0.56},
        "expression_digest": "ex" * 32,
        "cluster_digest": "cd" * 32,
        "criteria": dict(
            criteria
            if criteria is not None
            else {"tail_quantile": 0.95, "tail_share_min": 0.5, "axis_explained_min": 0.8}
        ),
        "reference_cells": 40000,
        "candidate_cells": len(cells),
        "axis_explained": axis_explained,
        "tail_share": round(sum(1 for row in rows[: len(cells)] if row["beyond_reference_tail"]) / len(cells), 6),
        "reference_median": 0.1,
        "reference_stdev": 1.15,
        "max_abs_z": 4.2,
        "rows": rows,
    }
    fields.update(overrides)
    return fields


# --------------------------------------------------------------------- model-call doubles


class RecordingGateway:
    """The inference backend a test controls: it records the prompt it was handed and answers from a queue."""

    def __init__(self, responses: Optional[Sequence[Mapping[str, Any]]] = None) -> None:
        self.responses = list(responses or [])
        self.prompts: list[dict[str, Any]] = []

    def complete(self, *, route: Any, prompt: dict[str, Any]) -> Any:
        from bioadapt_evidence.decision import ModelResponse

        self.prompts.append({"model_id": route.model_id, "prompt": prompt})
        fields = self.responses.pop(0) if self.responses else {}
        return ModelResponse(**fields)


class UnavailableGateway:
    """A backend that must never be reached: any call is the failure the test is looking for."""

    def complete(self, *, route: Any, prompt: dict[str, Any]) -> Any:
        raise AssertionError(f"the gateway was called for {route.model_id}; the decision should have refused")


def response(text: str = "answer", structured: Optional[dict[str, Any]] = None, **overrides: Any) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "text": text,
        "structured": structured or {"summary": "a rare CD14-low population"},
        "input_tokens": 120,
        "output_tokens": 40,
        "cost_usd": 0.02,
        "provider_snapshot": "snap_model_01",
    }
    fields.update(overrides)
    return fields
