"""Pseudobulk differential expression against section 7.8, on a design that can and cannot support it.

The dataset deliberately has four donors per group. That is enough to show where the effect is, and
not enough to clear a 5% FDR across a whole transcriptome - which is the point: the step records each
criterion separately so a reviewer can see which one failed rather than being handed a verdict.
"""

from __future__ import annotations

from dataclasses import replace

import pytest

from bioadapt_schemas.enums import ScreeningResult
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS

from bioadapt_scrnaseq.errors import StepFailure
from bioadapt_scrnaseq.matrix import CountMatrix
from bioadapt_scrnaseq.products import ClusterProduct, ExpressionProduct
from bioadapt_scrnaseq.steps import (
    SLOT_CLUSTERS,
    SLOT_COUNTS,
    SLOT_DE_TABLE,
    SLOT_EXPRESSION,
    DeContrast,
    pseudobulk_de_step,
)
from dataset import synthetic_dataset
from workflow_factories import (
    PERMUTATION_REPLICATES,
    SEEDS,
    clustering,
    de_steps,
    embedding_steps,
)


@pytest.fixture(scope="module")
def data():
    return synthetic_dataset()


@pytest.fixture(scope="module")
def de(data):
    return de_steps(data)


@pytest.fixture(scope="module")
def product(de):
    return de.product(SLOT_DE_TABLE)


def counts_product(data):
    return embedding_steps(data)["qc"].product(SLOT_COUNTS)


class TestDesignIsRecorded:
    def test_the_unit_is_the_donor_and_each_group_is_counted(self, de, product):
        assert product.unit == "donor"
        assert product.donors_per_group == {"case": 4, "control": 4}
        check = next(check for check in de.checks if check.name == "pseudobulk_unit_is_donor")
        assert check.result is ScreeningResult.PASS

    def test_the_contrast_design_is_written_into_the_table(self, de, product):
        detail = product.contrast_details["case_vs_control"]
        assert detail["design"] == "unpaired"
        assert detail["donors_a"] == detail["donors_b"] == 4
        assert detail["paired_donors"] == 0
        assert detail["cluster"] == "all_cells"
        assert detail["donors_tested"] == 8
        recorded = next(check for check in de.checks if check.name == "pseudobulk_design_recorded")
        assert "unpaired over 8 donors" in recorded.detail

    def test_a_donor_in_both_arms_is_tested_as_paired(self, data):
        """Paired is a property of the design, so the table has to say which test actually ran."""
        matrix = data.matrix
        cells = [
            replace(cell, group="case" if index % 2 == 0 else "control")
            for index, cell in enumerate(matrix.cells)
        ]
        interleaved = CountMatrix.build(
            cells=cells,
            counts={cell.cell_key: matrix.row(cell.cell_key) for cell in cells},
            genome=matrix.genome,
        )
        output = pseudobulk_de_step(
            ExpressionProduct(slot=SLOT_COUNTS, matrix=interleaved, transform="counts"),
            clustering(data),
            contrasts=[DeContrast(group_a="case", group_b="control")],
            permutation_replicates=PERMUTATION_REPLICATES,
            seed=SEEDS["pseudobulk_de"],
            thresholds=DEFAULT_THRESHOLDS,
        )
        detail = output.product(SLOT_DE_TABLE).contrast_details["case_vs_control"]
        assert detail["design"] == "paired"
        assert detail["paired_donors"] == detail["donors_tested"] == 8
        assert detail["donors_a"] == detail["donors_b"] == 8
        # A paired null randomises the sign of each donor's own difference, so it has 2**8 arrangements.
        # Re-dealing a constant label list would leave every permutation equal to the observed value and
        # report p = 1.0 for a real effect, which is the failure this assertion pins.
        assert detail["null_assignments"] == 256
        assert min(row.p_value for row in output.product(SLOT_DE_TABLE).rows) < 1.0

    def test_a_cluster_scoped_contrast_names_its_cluster(self, data, product):
        cluster = sorted(clustering(data).clusters)[0]
        scoped = de_steps(data, cluster=cluster)
        detail = scoped.product(SLOT_DE_TABLE).contrast_details[f"case_vs_control_in_{cluster}"]
        assert detail["cluster"] == cluster
        assert detail["donors_tested"] <= product.contrast_details["case_vs_control"]["donors_tested"]
        assert detail["donors_a"] >= 1 and detail["donors_b"] >= 1


class TestTheEffectIsFound:
    def test_the_group_programs_are_the_largest_effects(self, product):
        by_effect = sorted(product.rows, key=lambda row: (-abs(row.log2fc), row.gene))
        assert {row.gene for row in by_effect[:2]} == {"S100A8", "S100A9"}
        for row in by_effect[:2]:
            assert row.log2fc > 10.0
            assert row.detection_a == 1.0
            assert row.detection_b == 0.0
            assert row.donors_same_direction == row.donors_tested == 8
            assert row.donor_consistency == 1.0
        assert max(abs(row.log2fc) for row in by_effect[2:]) < 1.0

    def test_the_p_ordering_is_not_an_effect_ordering(self, de, product):
        """Why section 7.8 reads its criteria jointly instead of taking the head of the table.

        A permutation p-value ranks the observed donor split among the few splits the null can reach.
        A gene whose eight donors nearly agree has a near-empty null, so its small split can outrank a
        marker that is switched on in one arm alone - the p-ordering carries no effect-size information.
        """
        by_p = sorted(product.rows, key=lambda row: (row.p_value, row.gene))
        switched_on = next(row for row in product.rows if row.gene == "S100A9")
        head = by_p[0]
        assert abs(head.log2fc) < 1.0 < abs(switched_on.log2fc)
        assert head.p_value < switched_on.p_value
        recorded = next(check for check in de.checks if check.name == "permutation_null_resolution")
        assert recorded.result is ScreeningResult.PASS
        assert "case_vs_control: 70" in recorded.detail

    def test_the_multiple_testing_strategy_is_named_not_implied(self, product):
        assert product.multiple_testing == "benjamini_hochberg_within_contrast"
        assert product.thresholds_version == DEFAULT_THRESHOLDS.version
        assert product.permutation_replicates == PERMUTATION_REPLICATES
        for row in product.rows:
            assert row.padj >= row.p_value - 1e-12

    def test_adjusted_values_are_monotone_in_the_p_values(self, product):
        by_p = sorted(product.rows, key=lambda row: row.p_value)
        assert [row.padj for row in by_p] == sorted(row.padj for row in by_p)

    def test_a_permutation_p_value_can_never_reach_zero(self, data, product):
        floor = 1.0 / (PERMUTATION_REPLICATES + 1.0)
        assert min(row.p_value for row in product.rows) >= floor
        assert 0.0 not in {row.p_value for row in product.rows}
        # One shuffle leaves only two answers: the drawn split was as extreme as the real one, or it was not.
        single = de_steps(data, permutations=1).product(SLOT_DE_TABLE)
        assert {row.p_value for row in single.rows} == {0.5, 1.0}

    def test_donor_direction_agreement_is_recorded_per_gene(self, product):
        for row in product.rows:
            assert 0 <= row.donors_same_direction <= row.donors_tested
            assert row.donors_tested == 8


class TestWhatTheTableMayClaim:
    def test_four_donors_cannot_clear_fdr_over_the_whole_transcriptome(self, product):
        """The honest outcome: the effect is real, the design cannot call it significant at 5% FDR."""
        assert product.supporting(DEFAULT_THRESHOLDS) == ()
        assert min(row.padj for row in product.rows) > DEFAULT_THRESHOLDS.de_fdr_max
        assert product.assess(DEFAULT_THRESHOLDS, "S100A8") == {
            "significant": False,
            "effect_size": True,
            "detected_in_group": True,
            "absent_in_excluded": True,
            "donor_consistent": True,
            "powered": True,
        }

    def test_a_test_narrow_enough_to_support_the_claim_does_support_it(self, data):
        scoped = de_steps(data, genes=("S100A8", "S100A9", "GAPDH")).product(SLOT_DE_TABLE)
        assert scoped.tested_genes == 3
        assert scoped.supporting(DEFAULT_THRESHOLDS) == ("S100A8", "S100A9")
        gapdh = scoped.assess(DEFAULT_THRESHOLDS, "GAPDH")
        assert gapdh["significant"] is True
        assert gapdh["effect_size"] is False

    def test_below_the_power_floor_no_claim_is_allowed(self, data):
        """Section 7.4: under the floor the comparison is descriptive, whatever the p-values say."""
        strict = DEFAULT_THRESHOLDS.model_copy(update={"min_n_per_group": 5})
        output = de_steps(data, thresholds=strict)
        warning = next(check for check in output.checks if check.name == "pseudobulk_power")
        assert warning.result is ScreeningResult.WARN
        assert "min_n_per_group=5" in warning.detail
        product = output.product(SLOT_DE_TABLE)
        assert product.assess(strict, "S100A8")["powered"] is False
        assert product.supporting(strict) == ()

    def test_an_unknown_gene_has_no_flags(self, product):
        with pytest.raises(KeyError):
            product.assess(DEFAULT_THRESHOLDS, "NOT_TESTED")


class TestArmsThatNamePopulations:
    """Section 7.8 item 2 asks for the candidate measured against a *named excluded population*.

    Sample groups cannot express that comparison, so an arm may name a cluster instead. Every donor
    then contributes to both arms, which makes the test paired - the conservative design, because the
    donor difference is what is measured rather than which donor happened to be in which group.
    """

    @staticmethod
    def _cluster_of(data, labels, population):
        sizes: dict[str, int] = {}
        for row in labels.labels:
            if data.population_of[row.cell_key] == population:
                sizes[row.cluster] = sizes.get(row.cluster, 0) + 1
        return max(sizes, key=lambda cluster: (sizes[cluster], cluster))

    def test_a_cluster_arm_contrast_reads_the_population_markers(self, data):
        labels = clustering(data)
        b_cluster = self._cluster_of(data, labels, "b_cell")
        mono_cluster = self._cluster_of(data, labels, "monocyte")
        assert b_cluster != mono_cluster
        genes = ("MS4A1", "CD14", "GAPDH")
        product = de_steps(
            data,
            genes=genes,
            contrasts=[DeContrast(group_a=b_cluster, group_b=mono_cluster, arms="cluster")],
        ).product(SLOT_DE_TABLE)
        name = f"{b_cluster}_vs_{mono_cluster}_between_clusters"
        detail = product.contrast_details[name]
        assert detail["arms"] == "cluster"
        # Both populations were sequenced in every donor, so the donor is the block and the comparison
        # is paired: each donor's own b_cell-minus-monocyte difference is what the sign test shuffles.
        assert detail["design"] == "paired"
        assert detail["cluster"] == "all_cells"
        assert detail["donors_a"] == detail["donors_b"] == detail["paired_donors"] == 8
        assert detail["null_assignments"] == 256
        ms4a1 = next(row for row in product.rows if row.gene == "MS4A1")
        assert ms4a1.group_a == b_cluster and ms4a1.group_b == mono_cluster
        assert ms4a1.log2fc > 1.0 and ms4a1.detection_a == 1.0 and ms4a1.detection_b == 0.0
        cd14 = next(row for row in product.rows if row.gene == "CD14")
        assert cd14.log2fc < -1.0 and cd14.detection_a == 0.0 and cd14.detection_b == 1.0
        # The criteria name arm A as the population whose identity is being claimed, so a gene switched
        # *off* there fails both detection filters at once and can never support the claim. A caller
        # that wants CD14 as evidence has to put the monocyte arm in A.
        assert product.assess(DEFAULT_THRESHOLDS, "CD14") == {
            "significant": True,
            "effect_size": True,
            "detected_in_group": False,
            "absent_in_excluded": False,
            "donor_consistent": True,
            "powered": True,
        }
        assert product.supporting(DEFAULT_THRESHOLDS) == ("MS4A1",)

    def test_cluster_arms_that_miss_a_donor_degrade_to_the_unpaired_test(self, data):
        """T cells split along the group axis, so one of its clusters holds only six donors."""
        labels = clustering(data)
        t_cluster = self._cluster_of(data, labels, "t_cell")
        mono_cluster = self._cluster_of(data, labels, "monocyte")
        output = de_steps(
            data,
            genes=("CD3D", "CD14"),
            contrasts=[DeContrast(group_a=t_cluster, group_b=mono_cluster, arms="cluster")],
        )
        detail = output.product(SLOT_DE_TABLE).contrast_details[
            f"{t_cluster}_vs_{mono_cluster}_between_clusters"
        ]
        assert detail["design"] == "unpaired"
        assert detail["paired_donors"] < detail["donors_tested"]
        warning = next(check for check in output.checks if check.name == "pseudobulk_pairing")
        assert warning.result is ScreeningResult.WARN
        assert "both arms" in warning.detail

    def test_an_arms_value_that_is_not_a_known_kind_is_refused(self):
        with pytest.raises(StepFailure) as failure:
            DeContrast(group_a="c001", group_b="c002", arms="donor")
        assert failure.value.field == "contrasts"
        assert "arms must be one of" in failure.value.message

    def test_a_cluster_arm_contrast_cannot_also_scope_to_one_cluster(self):
        with pytest.raises(StepFailure) as failure:
            DeContrast(group_a="c001", group_b="c002", cluster="c001", arms="cluster")
        assert failure.value.field == "contrasts"
        assert "cannot also" in failure.value.message

    def test_both_arms_must_name_different_populations(self):
        with pytest.raises(StepFailure) as failure:
            DeContrast(group_a="case", group_b="case")
        assert failure.value.field == "contrasts"
        assert "different populations" in failure.value.message


class TestInputsAreRefused:
    def test_pseudobulk_needs_raw_counts(self, data):
        expression = embedding_steps(data)["normalize"].product(SLOT_EXPRESSION)
        with pytest.raises(StepFailure) as failure:
            pseudobulk_de_step(
                expression,
                clustering(data),
                contrasts=[DeContrast(group_a="case", group_b="control")],
                permutation_replicates=PERMUTATION_REPLICATES,
                seed=SEEDS["pseudobulk_de"],
                thresholds=DEFAULT_THRESHOLDS,
            )
        assert "log-normalised" in failure.value.message
        assert failure.value.error_class is ErrorClass.FAILED

    def test_a_cell_without_a_cluster_label_stops_the_step(self, data):
        labels = clustering(data)
        half = labels.labels[: len(labels.labels) // 2]
        partial = ClusterProduct(
            slot=SLOT_CLUSTERS,
            method=labels.method,
            resolution=labels.resolution,
            seed=labels.seed,
            graph_digest=labels.graph_digest,
            labels=half,
            modularity=labels.modularity,
            singleton_share=labels.singleton_share,
        )
        with pytest.raises(StepFailure) as failure:
            pseudobulk_de_step(
                counts_product(data),
                partial,
                contrasts=[DeContrast(group_a="case", group_b="control")],
                permutation_replicates=PERMUTATION_REPLICATES,
                seed=SEEDS["pseudobulk_de"],
                thresholds=DEFAULT_THRESHOLDS,
            )
        assert failure.value.field == "clusters"
        assert "no cluster label" in failure.value.message

    def test_a_contrast_naming_an_absent_group_is_refused(self, data):
        with pytest.raises(StepFailure) as failure:
            pseudobulk_de_step(
                counts_product(data),
                clustering(data),
                contrasts=[DeContrast(group_a="case", group_b="not_a_group")],
                permutation_replicates=PERMUTATION_REPLICATES,
                seed=SEEDS["pseudobulk_de"],
                thresholds=DEFAULT_THRESHOLDS,
            )
        assert failure.value.field == "contrasts"
        assert "not_a_group" in failure.value.message

    def test_the_stage0_numbers_are_inputs_not_defaults(self, data):
        for missing in ("permutation_replicates", "seed", "thresholds"):
            fields = {
                "permutation_replicates": PERMUTATION_REPLICATES,
                "seed": SEEDS["pseudobulk_de"],
                "thresholds": DEFAULT_THRESHOLDS,
            }
            fields[missing] = None
            with pytest.raises(StepFailure) as failure:
                pseudobulk_de_step(
                    counts_product(data),
                    clustering(data),
                    contrasts=[DeContrast(group_a="case", group_b="control")],
                    **fields,
                )
            assert failure.value.error_class is ErrorClass.NEEDS_INPUT
            assert failure.value.field == missing
            assert "appendix C1" in failure.value.message


class TestRepeatability:
    def test_the_same_seed_reproduces_the_table_byte_for_byte(self, data):
        assert de_steps(data).product(SLOT_DE_TABLE).json_bytes() == (
            de_steps(data).product(SLOT_DE_TABLE).json_bytes()
        )

    def test_a_different_seed_is_a_different_result(self, data):
        first = de_steps(data).product(SLOT_DE_TABLE)
        second = de_steps(data, seed=SEEDS["pseudobulk_de"] + 1).product(SLOT_DE_TABLE)
        assert first.file_sha256() != second.file_sha256()
        assert second.seed == SEEDS["pseudobulk_de"] + 1
