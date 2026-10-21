"""Drafting one candidate's evidence card from the products a run stored (sections 7.2-7.8, 9.1, 5.4).

The products here are typed out as the JSON a reviewer would open rather than produced by a workflow, so
what these tests hold is the reader's side of the contract: which verdicts it derives from which numbers,
which measurements it refuses to invent an answer for, and what it will not let a draft say. The run where
the reference implementation's own bytes are read is the root integration test.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Mapping, Optional, Sequence, Union

import pytest

import evidence_factories as E
from bioadapt_evidence.assets import SCHEMA_BY_SLOT
from bioadapt_evidence.cards import (
    DEFAULT_BLOCKING_SHARE,
    MAX_CARD_GENES,
    CardDraft,
    EvidenceCardService,
    SupportRule,
    card_id_for,
)
from bioadapt_evidence.errors import (
    CardUnderReview,
    ContractDrift,
    MissingProduct,
    TriggerOnHeldOutCells,
)
from bioadapt_schemas.entities import CandidateSet
from bioadapt_schemas.enums import (
    ArtifactRisk,
    CandidateType,
    EvidenceKind,
    EvidencePartition,
    EvidenceRole,
    FieldSource,
    FinalStatus,
    HypothesisType,
    LabelLevel,
    ScreeningResult,
    SignOff,
    UnknownReason,
)
from bioadapt_schemas.evidence import Evidence
from bioadapt_schemas.gates import audited_numeric_paths, numeric_provenance_violations
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS, Thresholds
from bioadapt_storage.catalog import Catalog
from bioadapt_storage.objectstore import InMemoryObjectStore

FOCUS = "rare_state"
OTHER = "cd14_monocyte"
THIRD = "naive_t"
HELD = "held_out_state"

#: A partition big enough that a 60-cell set is rare by the recorded rule: 60 / 6660 is under the 0.01
#: ceiling, while the same set in a smaller partition would read as common.
SIZES: dict[str, int] = {FOCUS: 60, OTHER: 4000, THIRD: 2600}

SLOT_NODES = {
    "clusters": "clustering",
    "markers": "markers",
    "de_table": "pseudobulk_de",
    "stability": "robustness",
    "coverage": "coverage",
    "state_axis": "state_axis",
    "qc_flags": "qc",
}

ProductOverride = Union[None, Mapping[str, Any], Callable[[dict[str, str], str, list[str]], Mapping[str, Any]]]


def standard_labels() -> dict[str, str]:
    return E.partition_labels(*(E.labels_block(name, size) for name, size in SIZES.items()))


def held_out_labels() -> dict[str, str]:
    """The standard partition plus a population that only exists on the held-out donor."""
    return E.partition_labels(
        *(E.labels_block(name, size) for name, size in SIZES.items()),
        E.labels_block(HELD, 60, specimens=E.CONFIRMATION_SPECIMENS),
    )


def run_products(partition: Mapping[str, str], focus: str) -> dict[str, Mapping[str, Any]]:
    """The seven products a candidate can be read from, all describing the same partition."""
    cells = E.members_of(partition, focus)
    details = {
        **E.cluster_contrast(f"focus_vs_{OTHER}", focus, OTHER),
        **E.condition_contrast(),
    }
    return {
        "clusters": E.clusters_payload(partition),
        "markers": E.markers_payload([focus, OTHER]),
        "de_table": E.de_payload(details, [E.de_table(focus, OTHER), E.de_table("stimulated", "control")]),
        "stability": E.stability_payload(focus, baseline_cells=len(cells)),
        "coverage": E.coverage_payload(cells),
        "state_axis": E.state_axis_payload(cells),
        "qc_flags": E.qc_payload({cell: () for cell in cells}),
    }


@dataclass
class Run:
    """One run's stored products, the service that reads them and the candidate they describe."""

    catalog: Catalog
    objects: InMemoryObjectStore
    partition: dict[str, str]
    focus: CandidateSet
    assets: dict[str, str]
    service: EvidenceCardService

    @property
    def cells(self) -> list[str]:
        return E.members_of(self.partition, self.focus.display_name)

    def draft(self, candidate: Optional[CandidateSet] = None) -> CardDraft:
        return self.service.draft(candidate or self.focus)

    def records(self, draft: CardDraft, kind: EvidenceKind, role: Optional[EvidenceRole] = None) -> list[Evidence]:
        return [
            record
            for record in draft.card.evidence.values()
            if record.kind is kind and (role is None or record.role is role)
        ]

    def record(self, draft: CardDraft, kind: EvidenceKind, role: Optional[EvidenceRole] = None) -> Evidence:
        found = [
            record
            for record in self.records(draft, kind)
            if role is None or record.role is role
        ]
        assert len(found) == 1, f"expected one {kind} record{' as ' + role.value if role else ''}, got {len(found)}"
        return found[0]


def harness(
    labels: Optional[Mapping[str, str]] = None,
    *,
    focus: str = FOCUS,
    replace: Optional[Mapping[str, ProductOverride]] = None,
    **service_kwargs: Any,
) -> Run:
    """A run whose products are the standard set with the named slots replaced or dropped.

    A replacement written as a callable receives the partition, the focus cluster and the focus cells, so
    a test that needs one product to say something different still reads its cell keys off the partition.
    """
    partition = dict(labels if labels is not None else standard_labels())
    products = dict(run_products(partition, focus))
    cells = E.members_of(partition, focus)
    for slot, override in (replace or {}).items():
        if override is None:
            products.pop(slot, None)
        elif callable(override):
            products[slot] = override(partition, focus, cells)
        else:
            products[slot] = override
    objects = InMemoryObjectStore()
    catalog = E.catalog_with_run(objects)
    assets: dict[str, str] = {}
    for slot, payload in products.items():
        assets[slot] = E.write_product(
            catalog,
            objects,
            slot=slot,
            schema_version=SCHEMA_BY_SLOT[slot],
            payload=payload,
            node_id=SLOT_NODES[slot],
        )
    focus_candidate = E.candidate_for(partition, focus)
    service = EvidenceCardService(
        catalog=catalog, objects=objects, run_id=E.RUN_ID, **service_kwargs
    )
    return Run(
        catalog=catalog,
        objects=objects,
        partition=partition,
        focus=focus_candidate,
        assets=assets,
        service=service,
    )


def covered(partition: Mapping[str, str], focus: str, cells: list[str]) -> Mapping[str, Any]:
    """A reference that does reach these cells: the ratio stays under the recorded cut."""
    return E.coverage_payload(cells, distance=0.4)


def flags_for(cells: Sequence[str], flagged: int, rule: str) -> dict[str, tuple[str, ...]]:
    """Every cell of the candidate scored, the first ``flagged`` of them carrying one rule's label.

    A screen reads the flagged share of the cells it scored, so a fixture that listed only the flagged
    cells would report a population entirely made of artefacts rather than one with a minority flag.
    """
    return {**{cell: (rule,) for cell in cells[:flagged]}, **{cell: () for cell in cells[flagged:]}}


# --------------------------------------------------------------------- what a draft may say


class TestADraftIsNotADecision:
    def test_the_draft_hypothesises_nothing_and_labels_nothing(self):
        card = harness().draft().card
        assert card.hypothesis_type is HypothesisType.UNKNOWN
        assert card.candidate_label is None
        assert card.label_level is LabelLevel.UNKNOWN_COARSE
        assert card.ontology.term_id is None
        assert card.final_status is FinalStatus.UNKNOWN
        assert card.sign_off is SignOff.NOT_SIGNED
        assert card.reviewer_id is None
        assert card.reviewed_at is None

    def test_the_ceiling_is_handed_to_the_reviewer_rather_than_written_into_the_card(self):
        run = harness()
        draft = run.draft()
        assert draft.ceiling is FinalStatus.PROVISIONAL
        assert "no independent confirmation evidence" in draft.gate_reasons
        assert "not screened: ambient" in draft.gate_reasons
        assert "not screened: stress_cycle" in draft.gate_reasons
        run.catalog.record_candidate(run.focus)
        run.service.persist(draft)
        assert run.catalog.get_card(draft.card_id).final_status is FinalStatus.UNKNOWN
        assert not hasattr(run.catalog.get_card(draft.card_id), "ceiling")

    def test_no_composition_test_is_invented_for_a_card_that_claims_no_composition_outlier(self):
        card = harness().draft().card
        assert card.composition_check.method is None
        assert "not run" in str(card.composition_check.result)

    def test_every_number_on_the_draft_cites_the_record_it_came_from(self):
        draft = harness().draft()
        card = draft.card
        assert numeric_provenance_violations(card) == []
        assert len(card.field_provenance) >= len(audited_numeric_paths(card))
        assert {entry.source for entry in card.field_provenance.values()} == {FieldSource.TOOL}

    def test_a_derived_field_names_the_asset_behind_the_numbers_it_derived(self):
        run = harness()
        draft = run.draft()
        card = draft.card
        paths = card.field_provenance
        composition = run.record(draft, EvidenceKind.COMPOSITION)
        assert paths[f"evidence.{composition.evidence_id}.content.size"].object_id == run.assets["clusters"]
        #: The donor count is the discovery node's index row, not something the partition hashed out.
        assert paths[f"evidence.{composition.evidence_id}.content.donor_count"].object_id == card.candidate_id
        assert paths["novelty_vs_coverage.distance_ratio"].object_id == run.assets["coverage"]
        assert paths["identity_vs_state.continuous_state_check.statistic"].object_id == run.assets["state_axis"]
        agreement = paths["stability_summary.results[0].cc_ari"]
        assert agreement.object_id == run.assets["stability"]
        assert "derived here" in str(agreement.note)

    def test_re_reading_the_same_bytes_gives_the_same_card(self):
        first = harness()
        second = harness(first.partition)
        assert first.draft().card.model_dump(mode="python") == second.draft().card.model_dump(mode="python")
        assert first.draft().card_id == card_id_for(first.focus.candidate_id)

    def test_the_status_reason_reports_what_the_numbers_could_and_could_not_settle(self):
        reason = harness().draft().card.status_reason
        assert "Artefacts: not screened: ambient" in reason
        assert "gap at ratio" in reason
        assert "axis tail" in reason
        assert "only trigger a look" in reason
        assert "recorded by a reviewer, never here" in reason


# --------------------------------------------------------------------- rarity


class TestRarityIsMeasured:
    def test_a_small_population_over_two_donors_is_rare_by_the_recorded_rule(self):
        run = harness()
        content = run.record(run.draft(), EvidenceKind.COMPOSITION).content
        assert content["size"] == 60
        assert content["partition_cells"] == 6660
        assert content["share"] == pytest.approx(0.009009)
        assert content["rare"] is True
        assert content["rare_rule_reasons"] == []
        assert content["thresholds_version"] == DEFAULT_THRESHOLDS.version

    def test_a_common_cluster_is_not_rare_however_the_caller_describes_it(self):
        run = harness(focus=OTHER)
        content = run.record(run.draft(), EvidenceKind.COMPOSITION).content
        assert content["size"] == 4000
        assert content["rare"] is False
        assert any("not below rare ceiling" in reason for reason in content["rare_rule_reasons"])

    def test_a_set_under_the_cell_minimum_is_not_rare_however_small_its_share(self):
        partition = E.partition_labels(E.labels_block("tiny_state", 20), E.labels_block(OTHER, 6640))
        run = harness(partition, focus="tiny_state")
        content = run.record(run.draft(), EvidenceKind.COMPOSITION).content
        assert content["share"] < DEFAULT_THRESHOLDS.rare_share_max
        assert content["rare"] is False
        assert any("below minimum 50" in reason for reason in content["rare_rule_reasons"])

    def test_a_single_donor_set_is_not_rare(self):
        partition = E.partition_labels(
            E.labels_block("one_donor", 60, specimens=["spc_donor_a"]), E.labels_block(OTHER, 6600)
        )
        run = harness(partition, focus="one_donor")
        content = run.record(run.draft(), EvidenceKind.COMPOSITION).content
        assert content["donor_count"] == 1
        assert content["rare"] is False
        assert any("donor_count 1 below minimum 2" in reason for reason in content["rare_rule_reasons"])


# --------------------------------------------------------------------- robustness


class TestStabilityIsRecomputedFromItsArms:
    def test_every_perturbation_class_is_judged_against_the_criteria_beside_it(self):
        run = harness()
        draft = run.draft()
        records = run.records(draft, EvidenceKind.STABILITY)
        assert sorted(str(r.content["kind"]) for r in records) == sorted(E.ALL_PERTURBATIONS)
        assert all(r.role is EvidenceRole.SUPPORTING for r in records)
        assert all(r.content["judgement"] == "pass" for r in records)
        assert all(r.content["cc_ari_median"] == 0.86 for r in records)
        assert all(r.content["criteria"]["cc_ari_min"] == 0.6 for r in records)
        assert draft.card.stability_summary is not None
        assert len(draft.card.stability_summary.results) == len(E.ALL_PERTURBATIONS)
        assert "no stability summary" not in draft.gate_reasons

    def test_a_class_whose_agreement_falls_below_the_cut_contradicts_the_candidate(self):
        run = harness(
            replace={
                "stability": lambda partition, focus, cells: E.stability_payload(
                    focus, baseline_cells=len(cells), numbers={"seed": {"cc_ari": 0.42}}
                )
            }
        )
        draft = run.draft()
        seed = next(r for r in run.records(draft, EvidenceKind.STABILITY) if r.content["kind"] == "seed")
        assert seed.role is EvidenceRole.CONTRADICTING
        assert seed.content["judgement"] == "fail"
        assert next(s for s in draft.card.stability_summary.results if s.kind.value == "seed").detail == {
            "judgement": "fail",
            "vanished_arms": 0,
        }

    def test_an_arm_that_vanished_is_a_fail_rather_than_a_missing_number(self):
        run = harness(
            replace={
                "stability": lambda partition, focus, cells: E.stability_payload(
                    focus, baseline_cells=len(cells), numbers={"subsampling": {"cells_compared": 0, "cc_ari": None}}
                )
            }
        )
        arm = next(r for r in run.records(run.draft(), EvidenceKind.STABILITY) if r.content["kind"] == "subsampling")
        assert arm.content["judgement"] == "fail"
        assert arm.content["vanished_arms"] == 1

    def test_a_small_cluster_only_passes_if_it_beats_its_own_permutation_null(self):
        run = harness(
            replace={
                "stability": lambda partition, focus, cells: E.stability_payload(
                    focus, baseline_cells=len(cells), numbers={"resolution": {"jaccard": 0.11}}
                )
            }
        )
        arm = next(r for r in run.records(run.draft(), EvidenceKind.STABILITY) if r.content["kind"] == "resolution")
        assert arm.content["jaccard_median"] == 0.11
        assert arm.content["permutation_jaccard_median"] == 0.12
        assert arm.content["judgement"] == "fail"

    def test_unmeasurable_numbers_are_reported_as_unmeasurable_not_as_a_pass(self):
        run = harness(
            replace={
                "stability": lambda partition, focus, cells: E.stability_payload(
                    focus,
                    baseline_cells=len(cells),
                    numbers={"feature_subset": {"cc_ari": None, "jaccard": None}},
                )
            }
        )
        arm = next(r for r in run.records(run.draft(), EvidenceKind.STABILITY) if r.content["kind"] == "feature_subset")
        assert arm.content["judgement"] == "unmeasurable"
        assert arm.role is EvidenceRole.CONTRADICTING

    def test_too_many_failing_classes_sends_the_stability_verdict_below_its_pass_ratio(self):
        run = harness(
            replace={
                "stability": lambda partition, focus, cells: E.stability_payload(
                    focus,
                    baseline_cells=len(cells),
                    numbers={kind: {"cc_ari": 0.1} for kind in ("seed", "resolution", "subsampling")},
                )
            }
        )
        draft = run.draft()
        assert any("passed 2/5 perturbations" in reason for reason in draft.gate_reasons)
        #: Two classes of the same question agree and three do not, so the card argues with itself first.
        assert draft.card.unresolved_conflict is True
        assert "unresolved conflict present" in draft.gate_reasons
        assert draft.ceiling is FinalStatus.CONFLICT

    def test_a_missing_robustness_product_leaves_stability_unmeasured(self):
        run = harness(replace={"stability": None})
        draft = run.draft()
        assert draft.card.stability_summary is None
        assert run.records(draft, EvidenceKind.STABILITY) == []
        assert any("no robustness product is stored" in note for note in draft.notes)
        assert "no stability summary" in draft.gate_reasons

    def test_a_product_that_never_measured_this_cluster_leaves_it_unmeasured(self):
        run = harness(replace={"stability": lambda partition, focus, cells: E.stability_payload(THIRD)})
        draft = run.draft()
        assert draft.card.stability_summary is None
        assert any(f"measured no perturbation arm for {FOCUS}" in note for note in draft.notes)

    def test_a_perturbation_class_the_plan_never_agreed_is_refused(self):
        run = harness(
            replace={
                "stability": lambda partition, focus, cells: E.stability_payload(
                    focus, baseline_cells=len(cells), kinds=("resolution", "magic_resizing")
                )
            }
        )
        with pytest.raises(ContractDrift, match="magic_resizing"):
            run.draft()

    def test_measurements_recorded_without_the_criteria_are_refused(self):
        run = harness(
            replace={
                "stability": lambda partition, focus, cells: E.stability_payload(
                    focus,
                    baseline_cells=len(cells),
                    criteria={"cc_ari_min": 0.6, "small_cluster_max_cells": 100},
                )
            }
        )
        with pytest.raises(ContractDrift, match="records no criteria for \\['jaccard_min'\\]"):
            run.draft()

    def test_an_incomplete_protocol_counts_only_against_an_approved_deviation(self):
        partial = {"stability": lambda partition, focus, cells: E.stability_payload(focus, kinds=("leave_one_donor",))}
        without = harness(replace=partial)
        draft = without.draft()
        assert any("protocol incomplete" in reason for reason in draft.gate_reasons)
        assert draft.card.stability_summary.approved_deviation is None

        approved = harness(
            replace={
                "stability": lambda partition, focus, cells: E.stability_payload(
                    focus,
                    kinds=("leave_one_donor",),
                    omitted_arms=["resolution=cpu_quota"],
                    deviation="dev-0007: resolution arm dropped under the node quota, approved by the PI",
                )
            }
        )
        settled = approved.draft()
        assert settled.card.stability_summary.approved_deviation.startswith("dev-0007")
        assert not any("protocol incomplete" in reason for reason in settled.gate_reasons)
        #: The arms it did read still have to be screened indirectly, and the omitted classes are recorded.
        batch = settled.card.artifact_screening.batch_specific
        assert batch.result is ScreeningResult.PASS
        assert "proxy for a batch_specific screen" in str(batch.detail)


# --------------------------------------------------------------------- markers and support


class TestMarkerSupport:
    def test_the_rank_table_is_a_trigger_never_a_support(self):
        run = harness()
        record = run.record(run.draft(), EvidenceKind.MARKER_SPECIFICITY, EvidenceRole.TRIGGER)
        assert record.content["rank_only"] is True
        assert record.content["genes"] == ["IL7R", "CXCL13", "PDCD1"]
        assert record.content["method"] == "rank_genes_groups_wilcoxon"

    def test_a_gene_meeting_every_identity_criterion_supports_the_candidate(self):
        run = harness()
        draft = run.draft()
        support = run.record(draft, EvidenceKind.MARKER_SPECIFICITY, EvidenceRole.SUPPORTING)
        assert support.content["qualifying_genes"] == ["IL7R"]
        assert [row["gene"] for row in support.content["genes"]] == ["IL7R"]
        assert support.content["excluded_population"] == OTHER
        assert support.content["design"] == "paired"
        assert "design_limit" not in support.content
        assert draft.support_met is True
        assert "support rule not met" not in draft.gate_reasons

    def test_a_contrast_aimed_the_other_way_tests_the_other_population(self):
        run = harness(
            replace={
                "de_table": lambda partition, focus, cells: E.de_payload(
                    {**E.cluster_contrast(f"{OTHER}_vs_{focus}", OTHER, focus)},
                    [E.de_table(OTHER, focus)],
                )
            }
        )
        draft = run.draft()
        assert run.records(draft, EvidenceKind.MARKER_SPECIFICITY, EvidenceRole.SUPPORTING) == []
        aimed = [
            record
            for record in run.records(draft, EvidenceKind.MARKER_SPECIFICITY, EvidenceRole.TRIGGER)
            if "orientation" in record.content
        ]
        assert len(aimed) == 1
        assert "arm B" in aimed[0].content["orientation"]
        #: Nothing was judged against section 7.8's criteria, so the reader reports that it did not try.
        assert draft.support_met is None

    def test_a_contrast_with_no_qualifying_gene_contradicts_and_caps_the_card(self):
        run = harness(
            replace={
                "de_table": lambda partition, focus, cells: E.de_payload(
                    {**E.cluster_contrast(f"focus_vs_{OTHER}", focus, OTHER), **E.condition_contrast()},
                    [E.weak_de_table(focus, OTHER), E.weak_de_table("stimulated", "control")],
                )
            }
        )
        draft = run.draft()
        against = run.record(draft, EvidenceKind.MARKER_SPECIFICITY, EvidenceRole.CONTRADICTING)
        assert against.content["qualifying_genes"] == []
        assert draft.support_met is False
        assert "support rule not met" in draft.gate_reasons

    def test_two_contrasts_that_disagree_leave_the_card_arguing_with_itself(self):
        run = harness(
            replace={
                "de_table": lambda partition, focus, cells: E.de_payload(
                    {
                        **E.cluster_contrast(f"focus_vs_{OTHER}", focus, OTHER),
                        **E.cluster_contrast(f"focus_vs_{THIRD}", focus, THIRD),
                        **E.condition_contrast(),
                    },
                    [
                        E.de_table(focus, OTHER),
                        E.weak_de_table(focus, THIRD),
                        E.weak_de_table("stimulated", "control"),
                    ],
                )
            }
        )
        draft = run.draft()
        assert draft.card.unresolved_conflict is True
        assert draft.card.unknown_reason is UnknownReason.QUALITY_LIMITED
        assert len(run.records(draft, EvidenceKind.MARKER_SPECIFICITY, EvidenceRole.SUPPORTING)) == 1
        assert len(run.records(draft, EvidenceKind.MARKER_SPECIFICITY, EvidenceRole.CONTRADICTING)) == 1
        assert draft.support_met is False

    def test_an_unpaired_design_says_so_on_the_record(self):
        run = harness(
            replace={
                "de_table": lambda partition, focus, cells: E.de_payload(
                    {
                        **E.cluster_contrast(f"focus_vs_{OTHER}", focus, OTHER, design="unpaired", donors=4),
                        **E.condition_contrast(),
                    },
                    [E.de_table(focus, OTHER), E.de_table("stimulated", "control")],
                )
            }
        )
        support = run.record(run.draft(), EvidenceKind.MARKER_SPECIFICITY, EvidenceRole.SUPPORTING)
        assert "arms do not share every donor" in support.content["design_limit"]

    def test_a_table_without_a_multiplicity_strategy_earns_no_support(self):
        run = harness(
            replace={
                "de_table": lambda partition, focus, cells: E.de_payload(
                    {**E.cluster_contrast(f"focus_vs_{OTHER}", focus, OTHER), **E.condition_contrast()},
                    [E.de_table(focus, OTHER), E.de_table("stimulated", "control")],
                    multiple_testing="none",
                )
            }
        )
        draft = run.draft()
        against = run.record(draft, EvidenceKind.MARKER_SPECIFICITY, EvidenceRole.CONTRADICTING)
        #: The per-gene criteria are met; the strategy that would let them be believed is not recorded.
        assert against.content["qualifying_genes"] == ["IL7R"]
        assert "multiplicity strategy not recorded" in against.content["support_rule_reasons"]
        assert draft.support_met is False
        assert "support rule not met" in draft.gate_reasons

    def test_only_the_qualifying_genes_that_reach_the_card_are_reproduced(self):
        genes = [f"GENE{index:02d}" for index in range(30)]
        rows = [
            {
                "gene": gene,
                "group_a": FOCUS,
                "group_b": OTHER,
                "log2fc": 1.5,
                "p_value": 1e-5,
                "padj": 5e-5,
                "detection_a": 0.9,
                "detection_b": 0.01,
                "donors_tested": 4,
                "donors_same_direction": 4,
            }
            for gene in genes
        ]
        run = harness(
            replace={
                "de_table": lambda partition, focus, cells: E.de_payload(
                    {**E.cluster_contrast(f"focus_vs_{OTHER}", FOCUS, OTHER), **E.condition_contrast()},
                    [rows, E.de_table("stimulated", "control")],
                ),
                "markers": lambda partition, focus, cells: E.markers_payload([focus], genes=genes),
            }
        )
        support = run.record(run.draft(), EvidenceKind.MARKER_SPECIFICITY, EvidenceRole.SUPPORTING)
        assert len(support.content["qualifying_genes"]) == 30
        assert len(support.content["genes"]) == MAX_CARD_GENES

    def test_a_group_below_the_design_power_floor_is_descriptive_only(self):
        run = harness(
            replace={
                "de_table": lambda partition, focus, cells: E.de_payload(
                    {**E.cluster_contrast(f"focus_vs_{OTHER}", focus, OTHER), **E.condition_contrast()},
                    [E.de_table(focus, OTHER), E.de_table("stimulated", "control")],
                    donors_per_group={FOCUS: 2, OTHER: 2, "stimulated": 2, "control": 2},
                )
            }
        )
        draft = run.draft()
        assert draft.underpowered_design is True
        assert any("descriptive only" in note for note in draft.notes)

    def test_underpowering_caps_a_composition_claim_because_it_compounds_the_same_weakness(self):
        run = harness(
            replace={
                "de_table": lambda partition, focus, cells: E.de_payload(
                    {**E.cluster_contrast(f"focus_vs_{OTHER}", focus, OTHER), **E.condition_contrast()},
                    [E.de_table(focus, OTHER), E.de_table("stimulated", "control")],
                    donors_per_group={FOCUS: 2, OTHER: 2, "stimulated": 2, "control": 2},
                )
            }
        )
        claim = E.candidate_for(run.partition, FOCUS, candidate_types=[CandidateType.C5_COMPOSITION_OUTLIER])
        draft = run.draft(claim)
        assert "design is underpowered for group comparisons" in draft.gate_reasons

    def test_a_marker_table_naming_no_gene_for_the_cluster_is_recorded_as_none(self):
        run = harness(replace={"markers": lambda partition, focus, cells: E.markers_payload([OTHER])})
        draft = run.draft()
        assert run.records(draft, EvidenceKind.MARKER_SPECIFICITY, EvidenceRole.TRIGGER) == []
        assert any("names no gene" in note for note in draft.notes)


# --------------------------------------------------------------------- artefact screening


class TestArtefactScreening:
    def test_every_named_risk_is_answered_even_where_no_tool_ran(self):
        screening = harness().draft().card.artifact_screening
        assert screening is not None
        assert {risk.value for risk in screening.blocked_by} == set()
        assert screening.doublet.result is ScreeningResult.PASS
        assert screening.low_quality.result is ScreeningResult.PASS
        assert screening.doublet.tool == "scrnaseq_qc"
        assert screening.doublet.tool_version == "1.0.0"
        assert "mito_fraction=rule=project_qc_note:qc_pbmc_reference" in screening.low_quality.threshold_source

    def test_two_risks_no_qc_metric_measures_are_read_from_arms_and_say_so(self):
        screening = harness().draft().card.artifact_screening
        for risk, arm in ((ArtifactRisk.BATCH_SPECIFIC, "leave_one_donor"), (ArtifactRisk.OVER_SPLIT, "resolution")):
            item = screening.items[risk]
            assert item.result is ScreeningResult.PASS
            assert f"proxy for a {risk.value} screen" in str(item.detail)
            assert f"the {arm} arm over 1 replicates" in str(item.detail)
            assert item.tool == "scrnaseq_robustness"

    def test_the_two_risks_with_no_screen_at_all_are_left_unscreened_rather_than_clear(self):
        screening = harness().draft().card.artifact_screening
        ambient = screening.items[ArtifactRisk.AMBIENT]
        assert ambient.result is ScreeningResult.NOT_RUN
        assert "no ambient screen is installed for this protocol" in str(ambient.detail)
        assert ambient.tool is None
        stress = screening.items[ArtifactRisk.STRESS_CYCLE]
        assert stress.result is ScreeningResult.NOT_RUN
        assert "no stress_cycle screen is installed" in str(stress.detail)
        #: An absent tool is recorded as a finding, so the artefact verdict never claims the set is clean.
        assert {risk.value for risk in screening.not_screened} == {"ambient", "stress_cycle"}

    def test_a_majority_of_flagged_cells_makes_the_population_the_artefact(self):
        def majority(partition, focus, cells):
            return E.qc_payload(flags_for(cells, 36, "mito_fraction"))

        run = harness(replace={"qc_flags": majority})
        draft = run.draft()
        assert draft.card.artifact_screening.low_quality.result is ScreeningResult.FAIL
        assert draft.blocking == ("low_quality",)
        against = run.record(draft, EvidenceKind.QUALITY_SCREENING, EvidenceRole.CONTRADICTING)
        assert against.content["flagged_cells"] == 36
        assert against.content["flagged_share"] == pytest.approx(0.6)
        assert against.content["blocking_share"] == DEFAULT_BLOCKING_SHARE
        assert "blocked by low_quality" in draft.gate_reasons

    def test_a_minority_flag_is_a_warning_that_only_triggers_a_look(self):
        def some(partition, focus, cells):
            return E.qc_payload(flags_for(cells, 3, "doublet_score"))

        run = harness(replace={"qc_flags": some})
        draft = run.draft()
        assert draft.card.artifact_screening.doublet.result is ScreeningResult.WARN
        trigger = run.record(draft, EvidenceKind.DOUBLET_SCREENING, EvidenceRole.TRIGGER)
        assert trigger.content["flagged_share"] == pytest.approx(0.05)
        assert draft.blocking == ()

    def test_cells_the_qc_step_never_scored_are_counted_not_guessed(self):
        def partly(partition, focus, cells):
            return E.qc_payload({cell: () for cell in cells[:40]})

        run = harness(replace={"qc_flags": partly})
        doublet = run.record(run.draft(), EvidenceKind.DOUBLET_SCREENING, EvidenceRole.SUPPORTING)
        assert doublet.content["scored_cells"] == 40
        assert doublet.content["candidate_cells"] == 60
        assert doublet.content["unscored_cells"] == 20

    def test_a_missing_qc_product_is_itself_a_finding(self):
        run = harness(replace={"qc_flags": None})
        draft = run.draft()
        assert draft.card.artifact_screening is None
        assert any("no artefact risk was screened" in note for note in draft.notes)
        assert "artifact screening not run" in draft.gate_reasons
        #: C3 promises a screening record, so the class claim is short of its own evidence set.
        assert any("C3_rare_stable is missing its minimum evidence set" in reason for reason in draft.gate_reasons)
        assert draft.ceiling is FinalStatus.PROVISIONAL

    def test_a_qc_product_that_never_scored_this_candidate_screens_nothing(self):
        def elsewhere(partition, focus, cells):
            return E.qc_payload({cell: () for cell in E.members_of(partition, THIRD)[:5]})

        run = harness(replace={"qc_flags": elsewhere})
        draft = run.draft()
        assert draft.card.artifact_screening is None
        assert any("scored none of this candidate" in note for note in draft.notes)


# --------------------------------------------------------------------- coverage and the axis


class TestCoverageAndContinuousState:
    def test_a_reference_that_does_not_reach_these_cells_is_a_gap_not_a_claim(self):
        run = harness()
        draft = run.draft()
        coverage = draft.card.novelty_vs_coverage
        assert coverage.knn == 20
        assert coverage.reference_coverage_score is None
        assert coverage.distance_ratio == pytest.approx(3.387097)
        assert coverage.coverage_gap is True
        #: Novelty is the gate's verdict over four conditions, and the artefact condition never holds here.
        assert coverage.novelty_signal is False
        trigger = run.record(draft, EvidenceKind.REFERENCE_COVERAGE, EvidenceRole.TRIGGER)
        assert "artifacts not excluded" in trigger.content["novelty_reasons"]
        assert trigger.content["beyond_calibration_share"] == 1.0
        assert "No coverage score is defined" in coverage.note

    def test_a_covered_set_is_evidence_against_the_nothing_the_reference_knows_story(self):
        run = harness(replace={"coverage": covered})
        draft = run.draft()
        assert draft.card.novelty_vs_coverage.coverage_gap is False
        against = run.record(draft, EvidenceKind.REFERENCE_COVERAGE, EvidenceRole.CONTRADICTING)
        assert against.content["coverage_gap"] is False
        assert "reference covers this set" in against.content["novelty_reasons"]

    def test_a_zero_calibration_median_leaves_the_ratio_unanswered(self):
        run = harness(
            replace={
                "coverage": lambda partition, focus, cells: E.coverage_payload(cells, calibration=0.0)
            }
        )
        draft = run.draft()
        assert draft.card.novelty_vs_coverage.distance_ratio is None
        assert draft.card.novelty_vs_coverage.coverage_gap is False
        assert any("median distance zero" in note for note in draft.notes)

    def test_no_coverage_product_leaves_the_reference_reach_unknown(self):
        run = harness(replace={"coverage": None})
        draft = run.draft()
        assert draft.card.novelty_vs_coverage is None
        assert any("how far this candidate sits from the reference is unknown" in note for note in draft.notes)

    def test_a_coverage_product_pointed_at_another_candidate_measures_nothing(self):
        run = harness(
            replace={
                "coverage": lambda partition, focus, cells: E.coverage_payload(E.members_of(partition, THIRD))
            }
        )
        draft = run.draft()
        assert draft.card.novelty_vs_coverage is None
        assert any(f"measured no cell of {FOCUS}" in note for note in draft.notes)

    def test_criteria_the_coverage_step_forgot_to_record_are_refused(self):
        run = harness(
            replace={
                "coverage": lambda partition, focus, cells: E.coverage_payload(cells, criteria={"knn": 20})
            }
        )
        with pytest.raises(ContractDrift, match="no criteria for \\['distance_ratio_min'\\]"):
            run.draft()

    def test_a_population_at_the_end_of_a_known_gradient_is_not_read_as_identity(self):
        run = harness(replace={"state_axis": lambda partition, focus, cells: E.state_axis_payload(cells)})
        draft = run.draft()
        record = run.record(draft, EvidenceKind.CONTINUOUS_STATE, EvidenceRole.CONTRADICTING)
        assert record.content["is_axis_tail"] is True
        assert record.content["attributable_to_candidate"] is True
        assert draft.card.identity_vs_state.continuous_state_check.is_axis_tail is True
        against = run.record(draft, EvidenceKind.REFERENCE_COVERAGE, EvidenceRole.TRIGGER)
        assert "consistent with the tail of a known continuous axis" in against.content["novelty_reasons"]

    def test_a_set_that_is_not_the_axis_tail_supports_the_identity_reading(self):
        run = harness(
            replace={"state_axis": lambda partition, focus, cells: E.state_axis_payload(cells, tail=False)}
        )
        draft = run.draft()
        assert run.record(draft, EvidenceKind.CONTINUOUS_STATE, EvidenceRole.SUPPORTING).content["tail_share"] == 0.0
        assert draft.card.identity_vs_state.continuous_state_check.is_axis_tail is False

    def test_an_axis_drawn_over_several_populations_is_not_attributable_to_this_one(self):
        def pooled(partition, focus, cells):
            return E.state_axis_payload(cells, extra=E.members_of(partition, OTHER)[:200])

        run = harness(replace={"state_axis": pooled})
        draft = run.draft()
        record = run.record(draft, EvidenceKind.CONTINUOUS_STATE, EvidenceRole.TRIGGER)
        assert record.content["attributable_to_candidate"] is False
        assert record.content["is_axis_tail"] is None
        assert draft.card.identity_vs_state.continuous_state_check is None
        assert any("not attributable to it" in note for note in draft.notes)
        #: An answer that belongs to the pooled set cannot satisfy C3's own evidence set.
        assert any("continuous-state check recorded" in str(reason) for reason in draft.gate_reasons)

    def test_a_missing_state_axis_product_never_asked_the_question(self):
        run = harness(replace={"state_axis": None})
        draft = run.draft()
        assert draft.card.identity_vs_state.continuous_state_check is None
        assert any("the axis question was never asked" in note for note in draft.notes)


# --------------------------------------------------------------------- partitions


class TestPartitions:
    def test_support_measured_only_on_held_out_donors_is_confirmation(self):
        run = harness(labels=held_out_labels(), focus=HELD, replace={"coverage": covered})
        draft = run.draft()
        card = draft.card
        assert card.confirmation_evidence_ids
        assert set(card.confirmation_evidence_ids) <= set(card.supporting_evidence_ids)
        for evidence_id in card.confirmation_evidence_ids:
            assert card.evidence[evidence_id].partition is EvidencePartition.CONFIRMATION
        assert "no independent confirmation evidence" not in draft.gate_reasons
        #: Reading the held-out partition is what confirmation means here; it does not lift the artefact cap.
        assert draft.ceiling is FinalStatus.PROVISIONAL

    def test_a_gap_found_only_on_held_out_cells_is_circular_inference(self):
        run = harness(labels=held_out_labels(), focus=HELD)
        with pytest.raises(TriggerOnHeldOutCells, match="confirmation specimens"):
            run.draft()

    def test_a_condition_contrast_restricted_to_held_out_cells_is_refused(self):
        def scoped(partition, focus, cells):
            details = {
                **E.cluster_contrast(f"focus_vs_{OTHER}", focus, OTHER),
                **E.condition_contrast(scope=focus),
            }
            return E.de_payload(details, [E.de_table(focus, OTHER), E.de_table("stimulated", "control")])

        run = harness(
            labels=held_out_labels(),
            focus=HELD,
            replace={"coverage": covered, "de_table": scoped},
        )
        with pytest.raises(TriggerOnHeldOutCells, match="condition/stim_vs_ctrl"):
            run.draft()

    def test_a_measurement_spanning_both_partitions_confirms_nothing(self):
        run = harness(labels=held_out_labels(), focus=FOCUS, replace={"coverage": covered})
        draft = run.draft()
        assert any("span both partitions" in note for note in draft.notes)
        assert draft.card.confirmation_evidence_ids == []
        #: The candidate's own cells are discovery, so its stability and screening records stay discovery too.
        assert all(
            record.partition is EvidencePartition.DISCOVERY
            for record in run.records(draft, EvidenceKind.STABILITY)
        )

    def test_a_specimen_in_neither_declared_partition_is_named(self):
        partition = E.partition_labels(
            E.labels_block(FOCUS, 60, specimens=["spc_donor_a", "spc_donor_d"]),
            E.labels_block(OTHER, 6600),
        )
        run = harness(partition)
        draft = run.draft()
        assert any("neither partition" in note for note in draft.notes)
        assert any("spc_donor_d" in note for note in draft.notes)


# --------------------------------------------------------------------- what is refused


class TestContractsRefused:
    def test_a_candidate_the_stored_labels_do_not_contain_is_refused(self):
        partition = standard_labels()
        candidate = E.candidate_for(partition, THIRD)
        run = harness(replace={"clusters": lambda p, focus, cells: E.clusters_payload(
            {key: name for key, name in p.items() if name != THIRD}
        )})
        with pytest.raises(ContractDrift, match="has no membership"):
            run.draft(candidate)

    def test_a_candidate_whose_fingerprint_is_not_the_stored_membership_is_refused(self):
        #: A candidate that names the cluster but is hashed over somebody else's cells.
        elsewhere = E.members_of(E.partition_labels(E.labels_block("ghost_cluster", 60)), "ghost_cluster")
        stranger = E.candidate_from_cells(elsewhere, display_name=FOCUS)
        run = harness()
        assert stranger.member_fingerprint != run.focus.member_fingerprint
        assert stranger.size == run.focus.size
        with pytest.raises(ContractDrift, match="not the same set of cells"):
            run.draft(stranger)

    def test_a_candidate_that_declares_the_wrong_size_is_refused(self):
        run = harness()
        cells = E.members_of(run.partition, FOCUS)
        overgrown = E.candidate_from_cells(cells, display_name=FOCUS, size=len(cells) + 10)
        with pytest.raises(ContractDrift, match="declares 70 cells"):
            run.draft(overgrown)

    def test_a_candidate_from_another_run_is_refused(self):
        run = harness()
        cells = E.members_of(run.partition, FOCUS)
        foreign = E.candidate_from_cells(cells, display_name=FOCUS, run_id="run_other_01")
        with pytest.raises(ContractDrift, match="run_other_01"):
            run.draft(foreign)

    def test_a_missing_mandatory_product_is_a_needs_input_failure(self):
        run = harness(replace={"markers": None})
        with pytest.raises(MissingProduct, match="produced no 'markers' asset"):
            run.draft()

    def test_a_card_built_against_revised_thresholds_is_refused_before_it_is_filed(self):
        with pytest.raises(ContractDrift, match="cite threshold set"):
            harness(thresholds=Thresholds(version="v1-measured", confirmed=True))

    def test_a_plan_from_another_project_is_refused(self):
        with pytest.raises(ContractDrift, match="not to run"):
            harness(plan=E.plan(project_id="prj_elsewhere"))

    def test_a_draft_that_could_not_answer_for_its_numbers_is_refused(self, monkeypatch):
        monkeypatch.setattr("bioadapt_evidence.cards._Citations.for_card", lambda self, card: {})
        with pytest.raises(ContractDrift, match="fails the gates its own numbers set") as caught:
            harness().draft()
        assert caught.value.field == "field_provenance"


class TestCandidateClassesSetTheirOwnCeilings:
    def test_a_composition_outlier_that_never_ran_its_test_reaches_only_unknown(self):
        run = harness()
        claim = E.candidate_for(run.partition, FOCUS, candidate_types=[CandidateType.C5_COMPOSITION_OUTLIER])
        draft = run.draft(claim)
        assert draft.ceiling is FinalStatus.UNKNOWN
        assert any("C5_composition_outlier is missing its minimum evidence set" in r for r in draft.gate_reasons)

    def test_an_unassigned_claim_without_robustness_is_named_on_the_card(self):
        run = harness(replace={"stability": None})
        claim = E.candidate_for(run.partition, FOCUS, candidate_types=[CandidateType.C1_UNASSIGNED])
        draft = run.draft(claim)
        assert any("robustness protocol run" in reason for reason in draft.gate_reasons)
        assert draft.ceiling is FinalStatus.PROVISIONAL

    def test_a_reference_conflict_is_short_both_its_references_and_its_confirmation(self):
        run = harness()
        claim = E.candidate_for(run.partition, FOCUS, candidate_types=[CandidateType.C2_REFERENCE_CONFLICT])
        draft = run.draft(claim)
        missing = next(reason for reason in draft.gate_reasons if "C2_reference_conflict" in reason)
        assert "both reference sources' applicability recorded" in missing
        assert "independent confirmation recorded" in missing

    def test_a_cross_modal_claim_is_short_both_its_modalities_and_its_adjudication(self):
        run = harness()
        claim = E.candidate_for(run.partition, FOCUS, candidate_types=[CandidateType.C4_CROSS_MODAL_DISCORDANT])
        draft = run.draft(claim)
        missing = next(reason for reason in draft.gate_reasons if "C4_cross_modal_discordant" in reason)
        assert "per-modality independent evidence recorded" in missing
        assert "conflict adjudicated" in missing


# --------------------------------------------------------------------- filing


class TestFilingADraft:
    def test_the_drafts_come_from_the_registered_candidates_in_a_stable_order(self):
        run = harness()
        second = E.candidate_for(run.partition, OTHER)
        run.catalog.record_candidate(run.focus)
        run.catalog.record_candidate(second)
        drafts = run.service.drafts()
        ids = [item.card.candidate_id for item in drafts]
        assert ids == sorted(ids)
        assert set(ids) == {run.focus.candidate_id, second.candidate_id}
        assert {item.card.display_name for item in drafts} == {FOCUS, OTHER}

    def test_a_candidate_nobody_registered_has_no_card_to_be_reviewed_against(self):
        run = harness()
        assert run.service.drafts() == []

    def test_filing_a_draft_twice_is_a_no_op_rather_than_a_second_row_to_review(self):
        run = harness()
        draft = run.draft()
        run.service.persist(draft)
        run.service.persist(draft)
        rows = list(run.catalog.conn.execute('select card_id from "evidence_card"'))
        assert len(rows) == 1
        assert run.catalog.card_final_status(draft.card_id) == FinalStatus.UNKNOWN.value
        assert run.catalog.get_card_for_candidate(draft.candidate_id).card_id == draft.card_id

    def test_a_rebuild_that_read_the_numbers_differently_updates_the_filed_draft(self):
        run = harness()
        run.service.persist(run.draft())
        strict = EvidenceCardService(
            catalog=run.catalog, objects=run.objects, run_id=E.RUN_ID, support_rule=SupportRule(min_qualifying_markers=5)
        )
        rebuilt = strict.draft(run.focus)
        assert rebuilt.support_met is False
        strict.persist(rebuilt, annotation_version_id="ann_01")
        stored = run.catalog.get_card(rebuilt.card_id)
        rows = list(run.catalog.conn.execute("select annotation_version_id from evidence_card"))
        assert len(rows) == 1
        assert rows[0]["annotation_version_id"] == "ann_01"
        #: One marker of a population that had to be read against two other populations is not a marker of it.
        read_again = next(r for r in stored.evidence.values() if "min_qualifying_markers" in r.content)
        assert read_again.content["min_qualifying_markers"] == 5
        assert read_again.role is EvidenceRole.CONTRADICTING

    def test_a_rebuild_does_not_overwrite_a_card_somebody_has_acted_on(self):
        run = harness()
        draft = run.draft()
        run.service.persist(draft)
        run.catalog.update_card(
            draft.card.model_copy(update={"reviewer_id": E.ANALYST, "review_reason": "wants the donor breakdown"})
        )
        with pytest.raises(CardUnderReview, match=f"reviewer {E.ANALYST}") as caught:
            run.service.persist(run.draft())
        assert caught.value.field == "final_status"
        assert run.catalog.get_card(draft.card_id).review_reason == "wants the donor breakdown"

    def test_a_signed_card_is_protected_by_the_same_rule(self):
        run = harness()
        draft = run.draft()
        run.service.persist(draft)
        run.catalog.update_card(draft.card.model_copy(update={"sign_off": SignOff.FULL}))
        with pytest.raises(CardUnderReview, match="cannot overwrite a card somebody has acted on"):
            run.service.persist(run.draft())
