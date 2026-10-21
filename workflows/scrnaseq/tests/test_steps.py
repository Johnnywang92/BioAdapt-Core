"""The reference executor's steps, tested against a dataset whose truth is known by construction.

Each test names the plan section it holds the step to and asserts a number rather than a shape:
"QC marks exactly the cells that were damaged" is a test, "QC returns a product" is not.
"""

from __future__ import annotations

import math

import pytest
from pydantic import ValidationError

from bioadapt_policies.qc_policy import QcAction
from bioadapt_schemas.enums import ScreeningResult
from bioadapt_schemas.errors import ErrorClass

from bioadapt_scrnaseq.errors import StepFailure
from bioadapt_scrnaseq.numeric import build_adjacency, modularity as modularity_of
from bioadapt_scrnaseq.steps import (
    SENSITIVITY_BASELINE,
    SENSITIVITY_EXCLUDING_MARKED,
    SLOT_CLUSTERS,
    SLOT_COUNTS,
    SLOT_EMBEDDING,
    SLOT_EXPRESSION,
    SLOT_FEATURES,
    SLOT_GRAPH,
    SLOT_MARKERS,
    SLOT_QC_FLAGS,
    cluster_step,
    graph_weights,
    hvg_step,
    markers_step,
    neighbors_step,
    normalize_step,
    pca_step,
)
from dataset import (
    ALL_GENES,
    BACKGROUND_GENES,
    GROUP_PROGRAMS,
    MITO_GENES,
    POPULATION_PROGRAMS,
    synthetic_dataset,
)
from workflow_factories import (
    MAX_MEAN,
    MIN_MEAN,
    MIN_VARIANCE,
    N_COMPONENTS,
    N_NEIGHBOURS,
    N_TOP_FEATURES,
    RESOLUTION,
    SEEDS,
    TARGET_SUM,
    TOP_MARKERS,
    clustering,
    drop_policy,
    embedding_steps,
    qc_policy,
    qc_step_for,
)


@pytest.fixture(scope="module")
def data():
    return synthetic_dataset()


@pytest.fixture(scope="module")
def steps(data):
    return embedding_steps(data)


def flagged_cells(product) -> set[str]:
    return {row.cell_key for row in product.rows if row.flags}


def fired_by(product, rule: str) -> set[str]:
    return {row.cell_key for row in product.rows if rule in row.flags}


class TestQualityControl:
    """Section 7.7: a versioned policy that marks cells, with the sensitivity contrast beside it."""

    def test_marks_exactly_the_planted_defects(self, data):
        product = qc_step_for(data).product(SLOT_QC_FLAGS)
        assert flagged_cells(product) == data.planted_defects
        assert product.marked_cells == len(data.planted_defects)

    def test_each_rule_fires_on_the_cells_it_names(self, data):
        product = qc_step_for(data).product(SLOT_QC_FLAGS)
        assert fired_by(product, "shallow_library") == set(data.shallow_cells)
        assert fired_by(product, "few_genes") == set(data.shallow_cells)
        assert fired_by(product, "deep_library") == set(data.doublet_cells)
        assert fired_by(product, "mito_high") == set(data.high_mito_cells)
        assert fired_by(product, "doublet_like") == set(data.doublet_cells)
        assert fired_by(product, "ambient_like") == set(data.ambient_cells)
        assert rule_summary(product, "mito_high").threshold_source == "distribution_diagnostic"
        assert rule_summary(product, "mito_high").source_ref == "quantile_p99_mito"

    def test_no_cell_is_deleted_when_the_policy_only_marks(self, data):
        output = qc_step_for(data)
        product = output.product(SLOT_QC_FLAGS)
        assert product.actions == ("mark",)
        assert product.dropped == ()
        assert output.product(SLOT_COUNTS).matrix.n_cells == data.matrix.n_cells

    def test_reports_the_unfiltered_baseline_beside_the_marked_view(self, data):
        """Section 7.1 step 3: the unfiltered run is reported alongside, never instead."""
        product = qc_step_for(data).product(SLOT_QC_FLAGS)
        sensitivity = product.sensitivity
        assert set(sensitivity) == {SENSITIVITY_BASELINE, SENSITIVITY_EXCLUDING_MARKED}
        marked = len(data.planted_defects)
        baseline = sensitivity[SENSITIVITY_BASELINE]
        clean = sensitivity[SENSITIVITY_EXCLUDING_MARKED]
        assert baseline["cells"] == data.matrix.n_cells
        assert baseline["flagged_cells"] == float(marked)
        assert clean["cells"] == data.matrix.n_cells - marked
        assert clean["flagged_cells"] == 0.0
        assert baseline["median_total_counts"] > 0.0
        assert clean["mito_measured_cells"] == clean["cells"]
        assert product.payload()["sensitivity"][SENSITIVITY_BASELINE]["cells"] == data.matrix.n_cells

    def test_excluding_marked_cells_does_not_erase_any_donor(self, data):
        """Section 7.4: a rare population may not disappear because a filter took its only donor."""
        sensitivity = qc_step_for(data).product(SLOT_QC_FLAGS).sensitivity
        every_donor = float(len(data.matrix.donors()))
        assert sensitivity[SENSITIVITY_BASELINE]["donors"] == every_donor
        assert sensitivity[SENSITIVITY_EXCLUDING_MARKED]["donors"] == every_donor

    def test_a_flagged_share_over_the_ceiling_stops_the_run(self, data):
        with pytest.raises(StepFailure) as failure:
            qc_step_for(data, max_flagged_share=0.1)
        error = failure.value
        assert error.error_class is ErrorClass.FAILED
        assert error.is_stop_condition
        assert "not analysable" in error.message

    def test_a_stage0_number_is_an_input_not_a_default(self, data):
        for missing in ("thresholds_version", "max_flagged_share"):
            with pytest.raises(StepFailure) as failure:
                qc_step_for(data, **{missing: None})
            assert failure.value.error_class is ErrorClass.NEEDS_INPUT
            assert "appendix C1" in failure.value.message
            assert failure.value.field == missing

    def test_a_share_outside_zero_and_one_is_rejected(self, data):
        with pytest.raises(StepFailure) as failure:
            qc_step_for(data, max_flagged_share=1.5)
        assert failure.value.field == "max_flagged_share"

    def test_a_gene_set_naming_genes_outside_the_matrix_is_rejected(self, data):
        with pytest.raises(StepFailure) as failure:
            qc_step_for(data, mito_genes=("MT-CO1", "NOT_IN_THIS_GENOME"))
        assert failure.value.field == "mito_genes"
        assert "NOT_IN_THIS_GENOME" in failure.value.message

    def test_an_absent_screen_is_recorded_as_absent_not_as_passing(self, data):
        """A cell with no doublet score is not a clean cell; the run has to say it never looked."""
        output = qc_step_for(data, doublet_scores=None)
        product = output.product(SLOT_QC_FLAGS)
        assert {row.doublet_score for row in product.rows} == {None}
        assert fired_by(product, "doublet_like") == set()
        warning = next(check for check in output.checks if check.name == "qc_rules_not_evaluated")
        assert warning.result is ScreeningResult.WARN
        assert "doublet_like" in warning.detail
        assert "not the same as being below the bound" in warning.detail

    def test_fractions_are_measured_from_the_matrix_not_supplied_by_the_policy(self, data):
        product = qc_step_for(data).product(SLOT_QC_FLAGS)
        damaged = data.high_mito_cells[0]
        row = next(row for row in product.rows if row.cell_key == damaged)
        assert row.mitochondrial_fraction == pytest.approx(
            data.matrix.fraction_of(damaged, set(MITO_GENES)), abs=1e-12
        )
        assert row.mitochondrial_fraction > 0.4
        assert row.total_counts == pytest.approx(data.matrix.total_counts(damaged), abs=1e-9)
        assert row.detected_genes == data.matrix.detected_genes(damaged)
        without = qc_step_for(data, mito_genes=None, ribosomal_genes=None).product(SLOT_QC_FLAGS)
        assert {row.mitochondrial_fraction for row in without.rows} == {None}
        assert {row.ribosomal_fraction for row in without.rows} == {None}
        assert fired_by(without, "mito_high") == set()

    def test_deleting_cells_needs_approver_reason_and_report(self, data):
        """Section 7.7: deletion is a project owner's act, and the product carries all three parts."""
        output = qc_step_for(data, drop_policy())
        product = output.product(SLOT_QC_FLAGS)
        assert set(product.dropped) == set(data.doublet_cells)
        assert product.actions == ("mark", "drop")
        assert product.cell_deletion_approved_by == "project_owner_01"
        assert product.deletion_annotated_in_report is True
        assert output.product(SLOT_COUNTS).matrix.n_cells == data.matrix.n_cells - len(data.doublet_cells)
        detail = next(check.detail for check in output.checks if check.name == "qc_marks_by_default")
        assert "doublet_like" in detail and "project_owner_01" in detail

    def test_a_drop_rule_without_approval_cannot_be_built(self):
        dropping = qc_policy().rules[0].model_copy(update={"action": QcAction.DROP})
        with pytest.raises(ValidationError) as failure:
            qc_policy(rules=(dropping,))
        assert "no approving project owner" in str(failure.value)


def rule_summary(product, name: str):
    return next(summary for summary in product.rules if summary.name == name)


class TestNormalisation:
    def test_values_are_log2_of_library_scaled_counts(self, data, steps):
        matrix = steps["normalize"].product(SLOT_EXPRESSION).matrix
        cell = data.matrix.cells[0].cell_key
        total = data.matrix.total_counts(cell)
        normalised = matrix.row(cell)
        for gene, count in list(data.matrix.row(cell).items())[:12]:
            assert normalised[gene] == pytest.approx(math.log2(1.0 + count * TARGET_SUM / total), abs=1e-9)

    def test_the_round_trip_recovers_the_library_size(self, steps):
        check = next(check for check in steps["normalize"].checks if check.name == "library_size_normalised")
        assert check.result is ScreeningResult.PASS
        assert steps["normalize"].metrics["normalisation_drift"] < 1e-9

    def test_the_lossy_conversion_is_recorded_not_dropped(self, steps):
        """Section 4.1: counts to log1p loses information, so the run says so and keeps both sides."""
        product = steps["normalize"].product(SLOT_EXPRESSION)
        assert product.transform == "log2_cpm"
        assert not product.counts
        assert any("lossy" in warning and "raw_counts" in warning for warning in product.conversion_warnings)
        assert any("counts" in warning for warning in steps["normalize"].warnings)

    def test_target_sum_is_required_and_positive(self, data, steps):
        matrix = steps["qc"].product(SLOT_COUNTS).matrix
        with pytest.raises(StepFailure) as failure:
            normalize_step(matrix, target_sum=None)
        assert failure.value.error_class is ErrorClass.NEEDS_INPUT
        with pytest.raises(StepFailure) as failure:
            normalize_step(matrix, target_sum=0.0)
        assert failure.value.field == "target_sum"


class TestFeatureSelection:
    def test_ranks_the_programme_genes_first(self, steps):
        genes = steps["hvg"].product(SLOT_FEATURES).genes
        informative = {
            gene
            for program in list(POPULATION_PROGRAMS.values()) + list(GROUP_PROGRAMS.values())
            for gene in program
        }
        assert set(genes[: len(informative)]) == informative
        assert not set(genes[: len(informative)]) & set(BACKGROUND_GENES)
        product = steps["hvg"].product(SLOT_FEATURES)
        assert product.method == "logspace_variance_ranking"
        assert product.n_top_features == N_TOP_FEATURES
        assert len(product.genes) == len(set(product.genes))

    def test_refuses_a_counts_matrix(self, steps):
        counts = steps["qc"].product(SLOT_COUNTS)
        with pytest.raises(StepFailure) as failure:
            hvg_step(
                counts,
                n_top_features=N_TOP_FEATURES,
                min_mean=MIN_MEAN,
                max_mean=MAX_MEAN,
                min_variance=MIN_VARIANCE,
                seed=SEEDS["highly_variable_features"],
            )
        assert failure.value.field == "expression"
        assert "raw counts" in failure.value.message

    def test_records_the_width_it_was_asked_for_even_when_it_found_less(self, steps):
        expression = steps["normalize"].product(SLOT_EXPRESSION)
        output = hvg_step(
            expression,
            n_top_features=len(ALL_GENES) + 50,
            min_mean=MIN_MEAN,
            max_mean=MAX_MEAN,
            min_variance=MIN_VARIANCE,
            seed=SEEDS["highly_variable_features"],
        )
        warning = next(check for check in output.checks if check.name == "feature_filter_too_strict")
        assert warning.result is ScreeningResult.WARN
        product = output.product(SLOT_FEATURES)
        assert len(product.genes) < product.n_top_features

    def test_stops_when_the_band_excludes_the_whole_dataset(self, steps):
        expression = steps["normalize"].product(SLOT_EXPRESSION)
        with pytest.raises(StepFailure) as failure:
            hvg_step(
                expression,
                n_top_features=N_TOP_FEATURES,
                min_mean=MIN_MEAN,
                max_mean=MAX_MEAN,
                min_variance=1e9,
                seed=SEEDS["highly_variable_features"],
            )
        assert failure.value.error_class is ErrorClass.FAILED

    def test_says_the_ranking_is_not_a_fitted_mean_variance_model(self, steps):
        assert "no mean-variance model" in steps["hvg"].warnings[0]

    def test_keeps_the_statistics_behind_every_choice(self, steps):
        product = steps["hvg"].product(SLOT_FEATURES)
        assert {row.gene for row in product.stats} == set(product.genes)
        for row in product.stats:
            assert MIN_MEAN <= row.mean <= MAX_MEAN
            assert row.variance >= MIN_VARIANCE
            assert 0.0 < row.detection_rate <= 1.0


class TestEmbedding:
    def test_keeps_every_cell_and_only_the_selected_features(self, data, steps):
        embedding = steps["pca"].product(SLOT_EMBEDDING)
        assert len(embedding.vectors) == data.matrix.n_cells
        assert {row.component for row in embedding.loadings} == set(range(1, embedding.n_components + 1))
        assert {row.gene for row in embedding.loadings} <= set(steps["hvg"].product(SLOT_FEATURES).genes)
        assert all(len(row.values) == embedding.n_components for row in embedding.vectors)
        assert all(math.isfinite(value) for row in embedding.vectors for value in row.values)

    def test_explained_variance_is_normalised_over_the_retained_components(self, steps):
        embedding = steps["pca"].product(SLOT_EMBEDDING)
        assert embedding.n_components == len(embedding.explained_variance_ratio) == N_COMPONENTS
        assert sum(embedding.explained_variance_ratio) == pytest.approx(1.0, abs=1e-6)
        assert list(embedding.explained_variance_ratio) == sorted(embedding.explained_variance_ratio, reverse=True)
        check = next(check for check in steps["pca"].checks if check.name == "explained_variance_scope")
        assert check.result is ScreeningResult.PASS

    def test_is_byte_identical_when_the_seed_is_repeated(self, steps):
        expression = steps["normalize"].product(SLOT_EXPRESSION)
        features = steps["hvg"].product(SLOT_FEATURES)
        again = pca_step(expression, features, n_components=N_COMPONENTS, seed=SEEDS["pca"])
        assert again.product(SLOT_EMBEDDING).json_bytes() == steps["pca"].product(SLOT_EMBEDDING).json_bytes()

    def test_refuses_more_components_than_the_feature_space_holds(self, steps):
        expression = steps["normalize"].product(SLOT_EXPRESSION)
        features = hvg_step(
            expression,
            n_top_features=6,
            min_mean=MIN_MEAN,
            max_mean=MAX_MEAN,
            min_variance=MIN_VARIANCE,
            seed=SEEDS["highly_variable_features"],
        ).product(SLOT_FEATURES)
        with pytest.raises(StepFailure) as failure:
            pca_step(expression, features, n_components=10, seed=SEEDS["pca"])
        assert "components" in failure.value.message

    def test_loadings_are_ranked_by_absolute_weight(self, steps):
        embedding = steps["pca"].product(SLOT_EMBEDDING)
        for component in range(1, embedding.n_components + 1):
            weights = [abs(row.weight) for row in embedding.loadings if row.component == component]
            assert weights == sorted(weights, reverse=True)


class TestNeighbourhoodGraph:
    def test_holds_at_most_k_neighbours_and_no_self_loops(self, steps):
        graph = steps["neighbors"].product(SLOT_GRAPH)
        for row in graph.rows:
            assert 0 < len(row.neighbours) <= N_NEIGHBOURS
            assert all(neighbour != row.cell_key for neighbour, _ in row.neighbours)
        assert graph.k == N_NEIGHBOURS
        assert graph.embedding_source == SLOT_EMBEDDING
        assert graph.seed == SEEDS["neighbors"]

    def test_orders_neighbours_by_distance(self, steps):
        for row in steps["neighbors"].product(SLOT_GRAPH).rows:
            assert [distance for _neighbour, distance in row.neighbours] == sorted(
                distance for _neighbour, distance in row.neighbours
            )

    def test_weights_fall_as_distance_grows(self, steps):
        graph = steps["neighbors"].product(SLOT_GRAPH)
        weights = graph_weights(graph)
        for row in graph.rows:
            for neighbour, distance in row.neighbours:
                pair = (min(row.cell_key, neighbour), max(row.cell_key, neighbour))
                assert weights[pair] == pytest.approx(1.0 / (1.0 + distance), rel=1e-12)

    def test_refuses_a_k_the_cell_set_cannot_satisfy(self, steps):
        embedding = steps["pca"].product(SLOT_EMBEDDING)
        with pytest.raises(StepFailure) as failure:
            neighbors_step(embedding, k=len(embedding.vectors), seed=SEEDS["neighbors"])
        assert failure.value.field == "k"


class TestClustering:
    def test_recovers_the_populations_without_mixing_them(self, data):
        product = clustering(data)
        cells = {row.cell_key: row.cluster for row in product.labels}
        assert set(cells) == {cell.cell_key for cell in data.matrix.cells}
        for cluster, members in product.cells_by_cluster().items():
            populations = {data.population_of[cell] for cell in members}
            assert len(populations) == 1, f"{cluster} mixes {populations}"
        assert {data.population_of[cell] for cell in cells} == set(POPULATION_PROGRAMS)

    def test_names_the_graph_it_was_computed_over(self, data, steps):
        graph = steps["neighbors"].product(SLOT_GRAPH)
        assert steps["cluster"].product(SLOT_CLUSTERS).graph_digest == graph.file_sha256()

    def test_reports_the_modularity_of_its_own_partition(self, data, steps):
        graph = steps["neighbors"].product(SLOT_GRAPH)
        product = steps["cluster"].product(SLOT_CLUSTERS)
        nodes = sorted({row.cell_key for row in graph.rows})
        adjacency = build_adjacency(nodes, graph_weights(graph))
        assignment = {row.cell_key: row.cluster for row in product.labels}
        assert product.modularity == pytest.approx(modularity_of(assignment, adjacency, RESOLUTION), abs=1e-12)
        assert product.modularity > 0.5

    def test_is_deterministic_for_a_repeated_seed(self, data, steps):
        graph = steps["neighbors"].product(SLOT_GRAPH)
        again = cluster_step(graph, resolution=RESOLUTION, seed=SEEDS["cluster"])
        assert again.product(SLOT_CLUSTERS).json_bytes() == steps["cluster"].product(SLOT_CLUSTERS).json_bytes()

    def test_resolution_changes_the_granularity(self, data, steps):
        graph = steps["neighbors"].product(SLOT_GRAPH)
        coarse = cluster_step(graph, resolution=0.05, seed=SEEDS["cluster"]).product(SLOT_CLUSTERS)
        fine = cluster_step(graph, resolution=2.0, seed=SEEDS["cluster"]).product(SLOT_CLUSTERS)
        assert len(coarse.clusters) < len(fine.clusters)
        assert len(coarse.clusters) < len(clustering(data).clusters) or len(coarse.clusters) == 1

    def test_does_not_claim_leidens_guarantee(self, steps):
        product = steps["cluster"].product(SLOT_CLUSTERS)
        assert product.method == "greedy_modularity_local_moving"
        assert any("not Leiden" in warning for warning in steps["cluster"].warnings)


class TestMarkers:
    def test_top_ranked_genes_are_the_cluster_own_program(self, data):
        product = markers_step(
            embedding_steps(data)["normalize"].product(SLOT_EXPRESSION), clustering(data), top_n=TOP_MARKERS
        ).product(SLOT_MARKERS)
        by_cluster = clustering(data).cells_by_cluster()
        every_cell = len(data.matrix.cells)
        for cluster, members in by_cluster.items():
            population = {data.population_of[cell] for cell in members}
            assert len(population) == 1
            name = population.pop()
            program = set(POPULATION_PROGRAMS[name])
            # A gene unique to one population is detected outside the cluster exactly as often as that
            # population sits outside it - the second t_cell cluster is not a different cell type.
            outside_share = (len(data.expected_populations()[name]) - len(members)) / (every_cell - len(members))
            rows = [row for row in product.rows if row.cluster == cluster]
            assert len(rows) == TOP_MARKERS
            assert [row.rank for row in rows] == list(range(1, TOP_MARKERS + 1))
            assert {row.gene for row in rows[:3]} <= program
            assert all(row.detection_in == 1.0 for row in rows[:3])
            assert all(row.detection_out == pytest.approx(outside_share, abs=1e-12) for row in rows[:3])
            assert all(row.log2fc > 6.0 for row in rows[:3])

    def test_is_marked_as_a_ranking_not_as_identity_evidence(self, data):
        """Section 7.8 item 5: a marker ordering may not be read as cell identity on its own."""
        output = markers_step(
            embedding_steps(data)["normalize"].product(SLOT_EXPRESSION), clustering(data), top_n=TOP_MARKERS
        )
        product = output.product(SLOT_MARKERS)
        assert product.rank_only is True
        check = next(check for check in output.checks if check.name == "markers_are_rankings_only")
        assert "section 7.8 item 5" in check.detail

    def test_names_the_clustering_it_describes(self, data):
        labels = clustering(data)
        product = markers_step(
            embedding_steps(data)["normalize"].product(SLOT_EXPRESSION), labels, top_n=TOP_MARKERS
        ).product(SLOT_MARKERS)
        assert product.cluster_digest == labels.file_sha256()

    def test_top_n_is_required(self, data):
        with pytest.raises(StepFailure) as failure:
            markers_step(
                embedding_steps(data)["normalize"].product(SLOT_EXPRESSION), clustering(data), top_n=None
            )
        assert failure.value.error_class is ErrorClass.NEEDS_INPUT
