"""What a step hands over, in the shape the platform stores it: round trips, fingerprints, refusals.

Two digests live on every product on purpose (§5.6, §10.1). ``file_sha256`` is the byte identity of
this serialization and is what the index and the checkpoint compare; ``content_fingerprint`` is the
structural identity of the data behind it, which a reviewer recomputes from the asset alone. So the
tests assert both directions: that the bytes rebuild the same product with the same fingerprints, and
that the refusals fire before an unusable product can be stored at all - a step that found nothing has
to fail rather than write an empty table.
"""

from __future__ import annotations

import json
from dataclasses import replace
from typing import Any

import pytest

from bioadapt_schemas.entities import sha256_hex
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS

from bioadapt_scrnaseq.matrix import CountMatrix
from bioadapt_scrnaseq.products import (
    PRODUCT_SCHEMAS,
    CellLabelRow,
    CellQcRow,
    CellScoreProduct,
    CellScoreRow,
    CellVectorRow,
    ClusterProduct,
    DeProduct,
    DeRow,
    EmbeddingProduct,
    ExpressionProduct,
    FeatureSelectionProduct,
    MarkerProduct,
    NeighborProduct,
    NeighborRow,
    Product,
    QcProduct,
    read_product,
)
from bioadapt_scrnaseq.steps import (
    SLOT_CLUSTERS,
    SLOT_COUNTS,
    SLOT_DE_TABLE,
    SLOT_EMBEDDING,
    SLOT_EXPRESSION,
    SLOT_FEATURES,
    SLOT_GRAPH,
    SLOT_MARKERS,
    SLOT_QC_FLAGS,
)
from dataset import synthetic_dataset
from workflow_factories import de_steps, embedding_steps, markers

#: The slots the reference chain produces, in the order a run publishes them.
PIPELINE_SLOTS = (
    SLOT_QC_FLAGS,
    SLOT_COUNTS,
    SLOT_EXPRESSION,
    SLOT_FEATURES,
    SLOT_EMBEDDING,
    SLOT_GRAPH,
    SLOT_CLUSTERS,
    SLOT_MARKERS,
    SLOT_DE_TABLE,
)


@pytest.fixture(scope="module")
def data():
    return synthetic_dataset()


@pytest.fixture(scope="module")
def pipeline(data) -> dict[str, Product]:
    """Every product the reference chain emits, from the dataset whose truth is known by construction."""
    steps = embedding_steps(data)
    return {
        SLOT_QC_FLAGS: steps["qc"].product(SLOT_QC_FLAGS),
        SLOT_COUNTS: steps["qc"].product(SLOT_COUNTS),
        SLOT_EXPRESSION: steps["normalize"].product(SLOT_EXPRESSION),
        SLOT_FEATURES: steps["hvg"].product(SLOT_FEATURES),
        SLOT_EMBEDDING: steps["pca"].product(SLOT_EMBEDDING),
        SLOT_GRAPH: steps["neighbors"].product(SLOT_GRAPH),
        SLOT_CLUSTERS: steps["cluster"].product(SLOT_CLUSTERS),
        SLOT_MARKERS: markers(data).product(SLOT_MARKERS),
        SLOT_DE_TABLE: de_steps(data).product(SLOT_DE_TABLE),
    }


def qc_product(**overrides: Any) -> QcProduct:
    row = CellQcRow(
        cell_key="c1",
        total_counts=1000.0,
        detected_genes=500,
        mitochondrial_fraction=0.02,
        ribosomal_fraction=0.1,
        doublet_score=0.01,
        ambient_score=0.02,
        flags=("deep_library",),
    )
    fields: dict[str, Any] = {
        "slot": SLOT_QC_FLAGS,
        "policy_id": "qc_pbmc_reference",
        "policy_version": "1",
        "thresholds_version": DEFAULT_THRESHOLDS.version,
        "rows": (row,),
        "sensitivity": {"unfiltered_baseline": {"cells": 1.0}, "flagged_excluded": {"cells": 1.0}},
    }
    fields.update(overrides)
    return QcProduct(**fields)


def score_product(**overrides: Any) -> CellScoreProduct:
    """The one product the chain does not emit: a screen's per-cell score (§7.1 step 3)."""
    fields: dict[str, Any] = {
        "slot": "doublet_scores",
        "metric": "doublet",
        "source_tool": "scrublet",
        "source_tool_version": "0.2.3",
        "rows": (CellScoreRow(cell_key="c1", score=0.85), CellScoreRow(cell_key="c2", score=0.02)),
        "assumptions": ("expected doublet rate 0.05",),
    }
    fields.update(overrides)
    return CellScoreProduct(**fields)


def de_row(**overrides: Any) -> DeRow:
    fields: dict[str, Any] = {
        "gene": "GAPDH",
        "group_a": "case",
        "group_b": "control",
        "log2fc": 1.4,
        "p_value": 0.004,
        "padj": 0.02,
        "detection_a": 0.95,
        "detection_b": 0.1,
        "donors_tested": 4,
        "donors_same_direction": 4,
    }
    fields.update(overrides)
    return DeRow(**fields)


def de_table(**overrides: Any) -> DeProduct:
    fields: dict[str, Any] = {
        "slot": SLOT_DE_TABLE,
        "method": "pseudobulk_wilcoxon",
        "multiple_testing": "permutation + Benjamini-Hochberg",
        "thresholds_version": DEFAULT_THRESHOLDS.version,
        "permutation_replicates": 200,
        "seed": 23,
        "unit": "donor",
        "donors_per_group": {"case": 4, "control": 4},
        "rows": (de_row(), de_row(gene="MT-CO1", log2fc=0.1, p_value=0.6, padj=0.9, detection_a=0.5,
                                    detection_b=0.4, donors_same_direction=2)),
        "contrasts": (("case", "control"),),
        "contrast_details": {
            "case|control": {"group_a": "case", "group_b": "control", "n_a": 4, "n_b": 4, "shared": 0}
        },
    }
    fields.update(overrides)
    return DeProduct(**fields)


# ------------------------------------------------------------------ the two identities (§5.6, §10.1)


@pytest.mark.parametrize("slot", PIPELINE_SLOTS)
def test_every_stored_product_reads_back_as_the_same_bytes_and_the_same_fingerprint(slot, pipeline) -> None:
    """The asset is the record: rebuilding it from its own bytes may not change either digest."""
    original = pipeline[slot]
    raw = original.json_bytes()
    restored = read_product(original.schema_version, raw)
    assert restored.file_sha256() == original.file_sha256() == sha256_hex(raw)
    assert restored.content_fingerprint() == original.content_fingerprint()


def test_the_stored_document_names_the_slot_and_the_contract_it_follows(pipeline) -> None:
    document = json.loads(pipeline[SLOT_MARKERS].json_bytes())
    assert document["slot"] == SLOT_MARKERS
    assert document["schema_version"] == "markers_v1"
    assert "slot" in document and "markers" in document, "the payload sits beside the two metadata keys"


def test_the_slot_is_part_of_the_bytes_so_two_slots_cannot_share_an_asset_id(pipeline) -> None:
    """Section 5.2 derives the asset id from the content, so the slot has to be inside the content."""
    counts = pipeline[SLOT_COUNTS]
    renamed = replace(counts, slot="something_else")
    assert renamed.file_sha256() != counts.file_sha256()


def test_the_fingerprint_ignores_row_order_while_the_file_digest_does_not(pipeline) -> None:
    """§5.6: a fingerprint that moved when a table was re-sorted could not survive re-serialization."""
    qc = pipeline[SLOT_QC_FLAGS]
    shuffled = replace(qc, rows=tuple(reversed(qc.rows)))
    assert shuffled.content_fingerprint() == qc.content_fingerprint()
    assert shuffled.file_sha256() != qc.file_sha256()


def test_the_fingerprint_is_taken_over_the_rounded_numbers_the_asset_holds(pipeline) -> None:
    """Six decimals is what the bytes store, so a reviewer recomputing from them gets the same number."""
    expression = pipeline[SLOT_EXPRESSION]
    stored = json.loads(expression.json_bytes())
    values = [value for _key, row in stored["matrix"]["rows"] for _gene, value in row]
    assert expression.fingerprint_parts()["numeric_summaries"] == [("value_sum", round(sum(values), 6))]


def test_the_contract_registry_holds_one_class_per_version() -> None:
    assert len(PRODUCT_SCHEMAS) == 12
    assert sorted(PRODUCT_SCHEMAS) == sorted(cls.schema_version for cls in PRODUCT_SCHEMAS.values())
    for version, cls in PRODUCT_SCHEMAS.items():
        assert cls.schema_version == version


def test_read_product_refuses_a_container_whose_index_row_names_another_contract(pipeline) -> None:
    """§4.1: a driver may not read a blob and then guess which of its own products it was holding."""
    qc = pipeline[SLOT_QC_FLAGS]
    with pytest.raises(ValueError, match="indexed as"):
        read_product("expression_v1", qc.json_bytes())


def test_a_container_naming_a_contract_nobody_implements_is_refused(pipeline) -> None:
    """Both names have to agree *and* be one the workflow knows how to rebuild."""
    document = json.loads(pipeline[SLOT_QC_FLAGS].json_bytes())
    unimplemented = json.dumps({**document, "schema_version": "qc_flags_v9"}).encode()
    with pytest.raises(KeyError, match="no product class"):
        read_product("qc_flags_v9", unimplemented)


def test_an_empty_table_is_a_failure_rather_than_an_asset() -> None:
    empty_markers = MarkerProduct(slot=SLOT_MARKERS, method="rank", top_n=4, cluster_digest="d" * 64, rows=())
    with pytest.raises(ValueError, match="produced an empty table"):
        empty_markers.content_fingerprint()
    stored = json.dumps(
        {
            "slot": SLOT_MARKERS,
            "schema_version": "markers_v1",
            "method": "rank",
            "top_n": 4,
            "rank_only": True,
            "cluster_digest": "d" * 64,
            "markers": [],
        }
    ).encode()
    with pytest.raises(ValueError, match="produced an empty table"):
        read_product("markers_v1", stored).content_fingerprint()


# ------------------------------------------------------------------ what each contract insists on


def test_a_qc_product_that_removed_cells_has_to_name_the_owner_that_allowed_it() -> None:
    """§7.7: a label is the default, and removal is only ever reachable through an approval."""
    with pytest.raises(ValueError, match="section 7.7"):
        qc_product(dropped=("c1",), actions=("mark", "drop"))
    with pytest.raises(ValueError, match="annotated in the report"):
        qc_product(dropped=("c1",), actions=("mark", "drop"), cell_deletion_approved_by="project_owner_01")
    with pytest.raises(ValueError, match="marks-only"):
        qc_product(
            dropped=("c1",),
            actions=("mark",),
            cell_deletion_approved_by="project_owner_01",
            deletion_annotated_in_report=True,
        )
    kept = qc_product(
        dropped=("c1",),
        actions=("mark", "drop"),
        cell_deletion_approved_by="project_owner_01",
        deletion_annotated_in_report=True,
    )
    assert kept.marked_cells == 1


def test_a_qc_product_without_a_sensitivity_comparison_is_not_a_qc_result() -> None:
    with pytest.raises(ValueError, match="no filtering-sensitivity comparison"):
        qc_product(sensitivity={})
    with pytest.raises(ValueError, match="same columns"):
        qc_product(sensitivity={"a": {"cells": 1.0}, "b": {"donors": 1.0}})


def test_an_expression_product_cannot_hold_an_empty_matrix(data) -> None:
    empty = CountMatrix.build(cells=[], counts={}, genome=data.matrix.genome)
    with pytest.raises(ValueError, match="empty matrix"):
        ExpressionProduct(slot=SLOT_EXPRESSION, matrix=empty, transform="counts")


def test_a_conversion_warning_recorded_by_a_step_survives_the_asset(data) -> None:
    """§4.1: the warning is data, so whoever reads the asset alone can see what the conversion lost."""
    product = ExpressionProduct(
        slot=SLOT_EXPRESSION,
        matrix=data.matrix,
        transform="counts",
        conversion_warnings=("conversion h5ad->seurat is lossy: ['reductions', 'graphs', 'obsm']",),
    )
    restored = read_product("expression_v1", product.json_bytes())
    assert isinstance(restored, ExpressionProduct)
    assert restored.conversion_warnings == product.conversion_warnings
    assert restored.counts is True
    assert replace(restored, transform="log2_cpm").counts is False


def test_a_feature_set_that_repeats_a_gene_or_overruns_its_cap_is_refused() -> None:
    base = {"slot": SLOT_FEATURES, "method": "seurat_v3", "n_top_features": 2, "seed": 7}
    with pytest.raises(ValueError, match="must be unique"):
        FeatureSelectionProduct(**base, genes=("A", "A"))
    with pytest.raises(ValueError, match="under a cap of"):
        FeatureSelectionProduct(**base, genes=("A", "B", "C"))
    with pytest.raises(ValueError, match="no genes"):
        FeatureSelectionProduct(**base, genes=())


def test_an_embedding_that_does_not_match_its_own_component_count_is_refused() -> None:
    base = {"slot": SLOT_EMBEDDING, "method": "pca", "n_components": 2, "seed": 11}
    ragged = (CellVectorRow(cell_key="c1", values=(1.0, 0.5)), CellVectorRow(cell_key="c2", values=(0.2,)))
    with pytest.raises(ValueError, match="same number of components"):
        EmbeddingProduct(**base, vectors=ragged)
    with pytest.raises(ValueError, match="vectors have widths"):
        EmbeddingProduct(
            slot=SLOT_EMBEDDING, method="pca", n_components=3, seed=11,
            vectors=(CellVectorRow(cell_key="c1", values=(1.0, 0.5)),),
        )
    short = (CellVectorRow(cell_key="c1", values=(1.0, 0.5)),)
    with pytest.raises(ValueError, match="sum to about one"):
        EmbeddingProduct(**base, vectors=short, explained_variance_ratio=(0.7, 0.2))
    assert EmbeddingProduct(**base, vectors=short, explained_variance_ratio=(0.7, 0.3)).n_components == 2


def test_a_graph_that_loops_onto_itself_or_exceeds_k_is_refused() -> None:
    base = {"slot": SLOT_GRAPH, "k": 2, "metric": "euclidean", "seed": 13, "embedding_source": "pca"}
    with pytest.raises(ValueError, match="more than k=2"):
        NeighborProduct(
            **base, rows=(NeighborRow(cell_key="c1", neighbours=(("c2", 1.0), ("c3", 2.0), ("c4", 3.0))),)
        )
    with pytest.raises(ValueError, match="its own neighbour"):
        NeighborProduct(**base, rows=(NeighborRow(cell_key="c1", neighbours=(("c1", 0.0),)),))
    graph = NeighborProduct(**base, rows=(NeighborRow(cell_key="c1", neighbours=(("c2", 1.0), ("c3", 2.0))),))
    assert graph.edge_count == 2


def test_a_clustering_with_no_cells_is_refused_and_a_partition_is_reported_ordered() -> None:
    with pytest.raises(ValueError, match="returned no cells"):
        ClusterProduct(
            slot=SLOT_CLUSTERS, method="leiden", resolution=1.0, seed=17, graph_digest="d" * 64, labels=()
        )
    cluster = ClusterProduct(
        slot=SLOT_CLUSTERS,
        method="leiden",
        resolution=1.0,
        seed=17,
        graph_digest="d" * 64,
        labels=(
            CellLabelRow(cell_key="c2", cluster="1"),
            CellLabelRow(cell_key="c3", cluster="0"),
            CellLabelRow(cell_key="c1", cluster="1"),
        ),
    )
    assert cluster.clusters == ("0", "1")
    assert cluster.cells_by_cluster() == {"0": ("c3",), "1": ("c1", "c2")}
    assert json.loads(cluster.json_bytes())["n_clusters"] == 2


def test_a_screen_score_without_a_metric_a_tool_or_a_probability_is_refused() -> None:
    for bad in (-0.1, 1.5):
        with pytest.raises(ValueError, match="is not a probability"):
            score_product(rows=(CellScoreRow(cell_key="c1", score=bad),))
    with pytest.raises(ValueError, match="repeat a cell key"):
        score_product(rows=(CellScoreRow(cell_key="c1", score=0.1), CellScoreRow(cell_key="c1", score=0.2)))
    with pytest.raises(ValueError, match="name the metric"):
        score_product(metric="")
    with pytest.raises(ValueError, match="tool and version"):
        score_product(source_tool="")


def test_a_score_product_carries_the_tool_that_scored_the_cell() -> None:
    """§7.1 step 3 reads a screen's output; the asset has to say which tool and model produced it."""
    scores = score_product()
    restored = read_product("cell_score_v1", scores.json_bytes())
    assert isinstance(restored, CellScoreProduct)
    assert restored.scores() == {"c1": 0.85, "c2": 0.02}
    assert restored.source_tool == "scrublet" and restored.source_tool_version == "0.2.3"
    assert restored.assumptions == scores.assumptions


def test_a_marker_table_still_says_it_is_only_a_ranking(pipeline) -> None:
    """§7.8 item 5: nothing downstream may read a rank as identity evidence, so the flag travels."""
    produced = pipeline[SLOT_MARKERS]
    assert isinstance(produced, MarkerProduct) and produced.rank_only is True
    restored = read_product(produced.schema_version, produced.json_bytes())
    assert isinstance(restored, MarkerProduct) and restored.rank_only is True


def test_a_de_table_that_cannot_say_how_it_was_tested_is_refused() -> None:
    with pytest.raises(ValueError, match="multiple-testing strategy"):
        de_table(multiple_testing="")
    with pytest.raises(ValueError, match="threshold set"):
        de_table(thresholds_version="")
    with pytest.raises(ValueError, match="biological replicates, not cells"):
        de_table(unit="cell")
    with pytest.raises(ValueError, match="how many donors"):
        de_table(donors_per_group={})
    with pytest.raises(ValueError, match="without a recorded design"):
        de_table(contrast_details={})


def test_a_de_row_cannot_claim_more_agreeing_donors_than_it_tested() -> None:
    with pytest.raises(ValueError, match="more agreeing donors than tested"):
        de_table(rows=(de_row(donors_same_direction=5),))
    with pytest.raises(ValueError, match="must be probabilities"):
        de_table(rows=(de_row(p_value=1.2),))


def test_the_support_criteria_are_read_from_the_named_threshold_set() -> None:
    table = de_table()
    assert table.tested_genes == 2
    assert all(table.assess(DEFAULT_THRESHOLDS, "GAPDH").values())
    gapdh = table.assess(DEFAULT_THRESHOLDS, "GAPDH")
    assert gapdh["significant"] and gapdh["powered"] and gapdh["donor_consistent"]
    with pytest.raises(KeyError):
        table.assess(DEFAULT_THRESHOLDS, "NOT_IN_THE_TABLE")
    assert table.supporting(DEFAULT_THRESHOLDS) == ("GAPDH",)


def test_a_de_row_with_no_donors_tested_reports_no_consistency_rather_than_dividing() -> None:
    assert de_row(donors_tested=0, donors_same_direction=0).donor_consistency == 0.0
