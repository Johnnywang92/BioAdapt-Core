import pytest

from bioadapt_schemas.enums import (
    CandidateType,
    CrossModalStatus,
    EvidenceKind,
    EvidencePartition,
    EvidenceRole,
    FieldSource,
    FinalStatus,
    PerturbationKind,
    ScreeningResult,
)
from bioadapt_schemas.evidence import (
    ContinuousStateCheck,
    FieldProvenance,
    NoveltyVsCoverage,
    PerturbationResult,
    StabilitySummary,
    ScreeningItem,
)
from bioadapt_schemas.gates import (
    ALLOWED_NUMERIC_SOURCES,
    GateViolation,
    MarkerStatistics,
    artifact_verdict,
    audited_numeric_paths,
    enforce_card_status,
    expert_queue,
    final_status_ceiling,
    independent_support_groups,
    iter_numeric_leaves,
    marker_support_verdict,
    novelty_verdict,
    numeric_provenance_violations,
    priority_score,
    rare_verdict,
    stability_verdict,
    support_rule_verdict,
)
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS, Thresholds
from schemas_factories import (
    make_card,
    make_composition,
    make_evidence,
    make_screening,
    make_stability,
    screen_item,
)


class TestNumericLeafWalk:
    def test_booleans_are_not_numeric_evidence(self):
        leaves = dict(iter_numeric_leaves({"a": True, "b": 1, "c": 2.5, "d": "x", "e": None}))
        assert set(leaves) == {"b", "c"}

    def test_nested_lists_index_their_leaves(self):
        leaves = dict(iter_numeric_leaves({"r": [{"v": 3}, {"v": 4}]}))
        assert leaves["r[0].v"] == 3 and leaves["r[1].v"] == 4

    def test_audited_paths_cover_stability_evidence_and_coverage(self):
        paths = set(audited_numeric_paths(make_card()))
        assert any(p.startswith("stability_summary.results[") for p in paths)
        assert "novelty_vs_coverage.knn" in paths
        assert any(p.startswith("evidence.") for p in paths)
        assert "field_provenance" not in " ".join(paths)


class TestNumericProvenance:
    def test_only_tool_and_human_may_source_numbers(self):
        assert set(ALLOWED_NUMERIC_SOURCES) == {FieldSource.TOOL, FieldSource.HUMAN}

    def test_complete_card_provenance_passes(self):
        assert numeric_provenance_violations(make_card()) == []

    def test_a_model_sourced_number_is_a_violation(self):
        card = make_card(
            field_provenance={
                "stability_summary.results[0].cc_ari": FieldProvenance(
                    source=FieldSource.MODEL, object_id="model_answer"
                )
            }
        )
        violations = numeric_provenance_violations(card)
        assert any("cc_ari" in v and "model" in v for v in violations)

    def test_retrieved_numbers_are_not_allowed_either(self):
        card = make_card(
            field_provenance={
                "evidence.ev_support.content.fdr": FieldProvenance(
                    source=FieldSource.RETRIEVAL, object_id="claim_1"
                )
            }
        )
        assert any("fdr" in v for v in numeric_provenance_violations(card))

    def test_missing_provenance_is_reported_per_path(self):
        paths = audited_numeric_paths(make_card())
        card = make_card().model_copy(update={"field_provenance": {}})
        violations = numeric_provenance_violations(card)
        assert len(violations) == len(paths)
        assert all("no provenance entry" in v for v in violations)

    def test_wildcard_entries_can_cover_a_family_of_paths(self):
        card = make_card().model_copy(update={"field_provenance": {}})
        wildcard = FieldProvenance(source=FieldSource.TOOL, object_id="asset_tool_output")
        provenance = {
            path: wildcard
            for path in audited_numeric_paths(card)
            if not path.startswith("evidence.")
        }
        provenance["evidence.*.content.*"] = wildcard
        assert numeric_provenance_violations(card.model_copy(update={"field_provenance": provenance})) == []


class TestRareVerdict:
    def test_rare_group_bounds(self):
        assert rare_verdict(size=500, total_cells=100_000, donor_count=4).passed
        verdict = rare_verdict(size=500, total_cells=10_000, donor_count=4)
        assert not verdict.passed and "share" in verdict.reasons[0]

    def test_cell_and_donor_floors_are_enforced(self):
        verdict = rare_verdict(size=30, total_cells=100_000, donor_count=1)
        assert not verdict.passed
        assert any("size" in r for r in verdict.reasons)
        assert any("donor_count" in r for r in verdict.reasons)

    def test_zero_total_cells_is_not_secretly_rare(self):
        assert not rare_verdict(size=10, total_cells=0, donor_count=1).passed


class TestStabilityVerdict:
    def test_full_mandatory_protocol_passes(self):
        verdict = stability_verdict(make_stability())
        assert verdict.passed
        assert verdict.detail["ratio"] == 1.0

    def test_missing_perturbations_fail_without_a_deviation(self):
        summary = make_stability(
            results=[
                PerturbationResult(
                    kind=PerturbationKind.SEED, cc_ari=0.72, jaccard=0.68, replicates=5
                )
            ]
        )
        verdict = stability_verdict(summary)
        assert not verdict.passed
        assert len(verdict.detail["missing"]) == 4

    def test_approved_deviation_passes_but_is_recorded_as_a_note(self):
        summary = make_stability(
            results=[
                PerturbationResult(
                    kind=PerturbationKind.SEED, cc_ari=0.72, jaccard=0.68, replicates=5
                )
            ],
            approved_deviation="single donor project, waiver rev-12",
        )
        verdict = stability_verdict(summary)
        assert verdict.passed
        assert verdict.detail["notes"]

    def test_absent_scores_count_as_failures(self):
        summary = StabilitySummary(
            protocol_version="v1",
            results=[
                PerturbationResult(kind=kind, replicates=2) for kind in PerturbationKind.mandatory_protocol()
            ],
        )
        assert not stability_verdict(summary).passed

    def test_pass_ratio_threshold_is_configurable(self):
        results = [
            PerturbationResult(
                kind=kind, cc_ari=0.9 if index == 0 else 0.1, jaccard=0.9 if index == 0 else 0.1, replicates=2
            )
            for index, kind in enumerate(PerturbationKind.mandatory_protocol())
        ]
        summary = StabilitySummary(protocol_version="v1", results=results)
        assert not stability_verdict(summary, DEFAULT_THRESHOLDS).passed
        relaxed = stability_verdict(summary, Thresholds(stability_pass_ratio=0.2))
        assert relaxed.passed and relaxed.detail["ratio"] == 0.2

    def test_missing_summary_fails(self):
        assert not stability_verdict(None).passed


def marker(**overrides):
    fields = {
        "gene": "Sht2a",
        "fdr": 0.001,
        "log2fc": 1.8,
        "detection_frequency_in": 0.85,
        "detection_frequency_excluded": 0.02,
        "donor_direction_consistency": 0.9,
        "multiplicity_strategy": "benjamini-hochberg",
    }
    fields.update(overrides)
    return MarkerStatistics(**fields)


class TestMarkerRules:
    def test_compliant_marker_passes(self):
        assert marker_support_verdict(marker()).passed

    def test_every_section_7_8_bound_is_checked(self):
        cases = {
            "fdr": "fdr",
            "log2fc": "|log2fc|",
            "detection_frequency_in": "in-group detection",
            "detection_frequency_excluded": "excluded detection",
            "donor_direction_consistency": "donor consistency",
        }
        for field, needle in cases.items():
            verdict = marker_support_verdict(marker(**{field: 0.5 if field != "log2fc" else 0.3}))
            assert not verdict.passed, field
            assert any(needle in reason for reason in verdict.reasons), field
            assert all(reason.startswith("Sht2a:") for reason in verdict.reasons), field

    def test_negative_effect_sizes_are_judged_by_magnitude(self):
        assert not marker_support_verdict(marker(log2fc=-0.5)).passed

    def test_marker_statistics_are_frozen_and_strict(self):
        with pytest.raises(Exception):
            marker().fdr = 0.0
        with pytest.raises(Exception):
            MarkerStatistics(gene="g", fdr=0.1, log2fc=1.0, detection_frequency_in=0.9,
                             detection_frequency_excluded=0.01, donor_direction_consistency=0.9,
                             multiplicity_strategy="bh", p_value=0.1)

    def test_support_rule_needs_enough_qualifying_markers(self):
        assert support_rule_verdict([marker()]).passed
        assert not support_rule_verdict([]).passed
        verdict = support_rule_verdict([marker(), marker(gene="Cd74", fdr=0.4)], require_min_markers=2)
        assert not verdict.passed
        assert verdict.detail["qualifying"] == ["Sht2a"]

    def test_unrecorded_multiplicity_strategy_blocks_support(self):
        verdict = support_rule_verdict([marker(multiplicity_strategy="none")])
        assert not verdict.passed
        assert "multiplicity" in verdict.reasons[0]


class TestArtifactAndNoveltyVerdicts:
    def test_missing_screening_is_treated_as_unscreened(self):
        assert not artifact_verdict(None).passed

    def test_fail_and_not_run_risks_both_block(self):
        screening = make_screening(
            doublet=screen_item(ScreeningResult.FAIL),
            ambient=ScreeningItem(result=ScreeningResult.NOT_RUN),
        )
        verdict = artifact_verdict(screening)
        assert not verdict.passed
        assert len(verdict.reasons) == 2

    def test_warning_is_not_a_block_but_stays_visible(self):
        verdict = artifact_verdict(make_screening(doublet=screen_item(ScreeningResult.WARN)))
        assert verdict.passed

    def test_novelty_requires_coverage_gap_stability_and_artifact_exclusion(self):
        ok_stability = stability_verdict(make_stability())
        ok_artifacts = artifact_verdict(make_screening())
        coverage = NoveltyVsCoverage(knn=20, reference_coverage_score=0.01, distance_ratio=4.0, coverage_gap=True)
        assert novelty_verdict(coverage, ok_stability, ok_artifacts, None).passed

        assert not novelty_verdict(None, ok_stability, ok_artifacts, None).passed
        assert not novelty_verdict(
            NoveltyVsCoverage(knn=20, coverage_gap=False), ok_stability, ok_artifacts, None
        ).passed
        assert not novelty_verdict(coverage, stability_verdict(None), ok_artifacts, None).passed
        assert not novelty_verdict(coverage, ok_stability, artifact_verdict(None), None).passed
        tail = ContinuousStateCheck(method="pseudotime", is_axis_tail=True)
        assert not novelty_verdict(coverage, ok_stability, ok_artifacts, tail).passed


class TestStatusCeilings:
    def test_full_confirmation_reaches_supported(self):
        card = make_card(FinalStatus.SUPPORTED)
        verdict = final_status_ceiling(card)
        assert verdict.passed
        assert verdict.detail["ceiling"] is FinalStatus.SUPPORTED

    def test_unconfirmed_cards_are_capped_at_provisional(self):
        card = make_card()
        verdict = final_status_ceiling(card)
        assert verdict.detail["ceiling"] is FinalStatus.PROVISIONAL
        assert not verdict.passed
        enforce_card_status(card).detail["ceiling"] is FinalStatus.PROVISIONAL

    def test_unresolved_conflict_caps_below_provisional(self):
        card = make_card(unresolved_conflict=True)
        assert final_status_ceiling(card).detail["ceiling"] is FinalStatus.CONFLICT
        with pytest.raises(GateViolation, match="exceeds ceiling conflict"):
            enforce_card_status(card)

    def test_missing_supporting_evidence_caps_at_unknown(self):
        card = make_card(supporting_evidence_ids=[], confirmation_evidence_ids=[])
        assert final_status_ceiling(card).detail["ceiling"] is FinalStatus.UNKNOWN
        with pytest.raises(GateViolation, match="exceeds ceiling unknown"):
            enforce_card_status(card)

    def test_failed_support_rule_caps_at_provisional(self):
        card = make_card(FinalStatus.SUPPORTED)
        verdict = final_status_ceiling(card, support_met=False)
        assert verdict.detail["ceiling"] is FinalStatus.PROVISIONAL
        assert "support rule not met" in verdict.reasons

    def test_single_independence_group_is_noted_in_the_verdict(self):
        card = make_card(FinalStatus.SUPPORTED)
        assert independent_support_groups(card) == {"atlas_a", "holdout_donors"}
        assert not final_status_ceiling(card).detail["notes"]

        merged = card.model_copy(
            update={
                "evidence": {
                    eid: ev.model_copy(update={"independence_group": "same_tool"})
                    for eid, ev in card.evidence.items()
                }
            }
        )
        assert len(independent_support_groups(merged)) == 1
        assert final_status_ceiling(merged).detail["notes"]

    def test_underpowered_composition_candidates_are_capped(self):
        card = make_card(
            candidate_types=[CandidateType.C5_COMPOSITION_OUTLIER],
            composition_check=make_composition(),
        )
        verdict = final_status_ceiling(card, underpowered_design=True)
        assert verdict.detail["ceiling"] is FinalStatus.PROVISIONAL
        assert any("underpowered" in r for r in verdict.reasons)

    def test_a_candidate_class_without_its_evidence_set_is_capped_below_itself(self):
        """Section 7.2: the class label is a claim, and the claim's evidence set is part of it."""
        composed = make_card(
            candidate_types=[CandidateType.C5_COMPOSITION_OUTLIER], composition_check=make_composition()
        )
        #: C5 is a descriptive class: even with its full set it cannot read as an identity claim.
        assert final_status_ceiling(composed).detail["ceiling"] is FinalStatus.PROVISIONAL
        bare = make_card(candidate_types=[CandidateType.C5_COMPOSITION_OUTLIER])
        verdict = final_status_ceiling(bare)
        assert verdict.detail["ceiling"] is FinalStatus.UNKNOWN
        assert verdict.detail["candidate_classes"]["missing"]["C5_composition_outlier"] == [
            "composition method and result recorded",
            "multiplicity strategy recorded",
        ]
        #: C2's disagreement has to be shown to be a disagreement, from two independent sources.
        c2 = make_card(FinalStatus.SUPPORTED, candidate_types=[CandidateType.C2_REFERENCE_CONFLICT])
        capped = final_status_ceiling(c2)
        assert capped.detail["ceiling"] is FinalStatus.PROVISIONAL
        assert "C2_reference_conflict" in capped.detail["candidate_classes"]["missing"]

    def test_rejected_and_unknown_always_fit_their_ceiling(self):
        for status in (FinalStatus.REJECTED, FinalStatus.UNKNOWN):
            card = make_card(status, supporting_evidence_ids=[], confirmation_evidence_ids=[])
            enforce_card_status(card)


class TestEnforcement:
    def test_model_sourced_numbers_block_the_card(self):
        card = make_card(
            field_provenance={
                "novelty_vs_coverage.distance_ratio": FieldProvenance(
                    source=FieldSource.MODEL, object_id="model_answer"
                )
            }
        )
        with pytest.raises(GateViolation, match="sourced from model"):
            enforce_card_status(card)

    def test_supported_without_confirmation_is_refused(self):
        card = make_card(FinalStatus.SUPPORTED, supporting_evidence_ids=["ev_support"], confirmation_evidence_ids=[])
        with pytest.raises(GateViolation, match="supported exceeds ceiling"):
            enforce_card_status(card)

    def test_honest_provisional_card_is_returned_with_reasons(self):
        verdict = enforce_card_status(make_card())
        assert verdict.detail["ceiling"] is FinalStatus.PROVISIONAL


class TestExpertQueue:
    def test_scores_are_bounded_and_reward_client_asks(self):
        assert priority_score(impact=3, uncertainty=2, client_ask=False) == 6
        assert priority_score(impact=3, uncertainty=2, client_ask=True) == 8
        with pytest.raises(ValueError):
            priority_score(impact=4, uncertainty=0, client_ask=False)

    def test_queue_is_limited_and_deterministically_ordered(self):
        scores = [("card_b", 9), ("card_a", 9), ("card_c", 12), ("card_d", 1)]
        queue, observed = expert_queue(scores, limit=2)
        assert queue == ["card_c", "card_a"]
        assert observed == ["card_b", "card_d"]

    def test_thresholds_default_to_the_stage_zero_placeholder_set(self):
        assert DEFAULT_THRESHOLDS.is_unconfirmed_default_set
        assert DEFAULT_THRESHOLDS.version == "v0-unconfirmed"


def supporting(card, eid: str, kind, group: str):
    """Add one supporting record of a named kind: how a candidate class earns its evidence set."""
    record = make_evidence(eid, EvidenceRole.SUPPORTING, EvidencePartition.DISCOVERY, group, {}, kind)
    return card.model_copy(
        update={
            "evidence": {**card.evidence, eid: record},
            "supporting_evidence_ids": [*card.supporting_evidence_ids, eid],
        }
    )


class TestCandidateClasses:
    """Section 7.2: five candidate classes, each with its own minimum evidence set and ceiling."""

    def test_an_unassigned_group_needs_a_marker_before_it_can_be_read_as_one(self):
        card = make_card(FinalStatus.SUPPORTED, candidate_types=[CandidateType.C1_UNASSIGNED])
        verdict = final_status_ceiling(card)
        assert verdict.detail["candidate_classes"]["missing"]["C1_unassigned"] == ["marker support measured"]
        #: An incomplete set pulls the class one step down, so the claim reads provisional, not supported.
        assert verdict.detail["ceiling"] is FinalStatus.PROVISIONAL
        marked = supporting(card, "ev_marker", EvidenceKind.MARKER_SPECIFICITY, "panel_b")
        earned = final_status_ceiling(marked)
        assert earned.detail["candidate_classes"]["missing"] == {}
        assert earned.detail["ceiling"] is FinalStatus.SUPPORTED

    def test_a_reference_conflict_needs_both_sides_of_the_disagreement(self):
        card = make_card(FinalStatus.SUPPORTED, candidate_types=[CandidateType.C2_REFERENCE_CONFLICT])
        verdict = final_status_ceiling(card)
        assert sorted(verdict.detail["candidate_classes"]["missing"]["C2_reference_conflict"]) == sorted(
            ["both reference sources' applicability recorded", "exclusionary marker recorded"]
        )
        settled = supporting(card, "ev_ref_a", EvidenceKind.REFERENCE_MAPPING, "atlas_a")
        settled = supporting(settled, "ev_ref_b", EvidenceKind.REFERENCE_MAPPING, "atlas_b")
        settled = supporting(settled, "ev_excl", EvidenceKind.MARKER_SPECIFICITY, "panel_b")
        assert final_status_ceiling(settled).detail["ceiling"] is FinalStatus.SUPPORTED
        #: Two mappings that share an independence group are one opinion about two clusters.
        one_source = supporting(card, "ev_ref_a", EvidenceKind.REFERENCE_MAPPING, "atlas_a")
        one_source = supporting(one_source, "ev_ref_b", EvidenceKind.REFERENCE_MAPPING, "atlas_a")
        one_source = supporting(one_source, "ev_excl", EvidenceKind.MARKER_SPECIFICITY, "panel_b")
        assert final_status_ceiling(one_source).detail["candidate_classes"]["missing"][
            "C2_reference_conflict"
        ] == ["both reference sources' applicability recorded"]

    def test_a_cross_modal_claim_needs_both_modalities_and_a_ruling(self):
        card = make_card(FinalStatus.SUPPORTED, candidate_types=[CandidateType.C4_CROSS_MODAL_DISCORDANT])
        assert len(card.candidate_types) == 1
        missing = final_status_ceiling(card).detail["candidate_classes"]["missing"]
        assert sorted(missing["C4_cross_modal_discordant"]) == sorted(
            ["per-modality independent evidence recorded", "conflict adjudicated"]
        )
        adjudicated = card.model_copy(update={"cross_modal_status": CrossModalStatus.DISCORDANT})
        both = supporting(adjudicated, "ev_rna", EvidenceKind.CROSS_MODAL, "rna_atlas")
        both = supporting(both, "ev_atac", EvidenceKind.CROSS_MODAL, "atac_atlas")
        assert final_status_ceiling(both).detail["ceiling"] is FinalStatus.SUPPORTED
        #: A ruling that is still open is a conflict, and the class cannot talk its way out of that.
        open_conflict = both.model_copy(update={"unresolved_conflict": True})
        assert final_status_ceiling(open_conflict).detail["ceiling"] is FinalStatus.CONFLICT

    def test_a_rare_stable_claim_needs_the_donor_arm_of_the_protocol(self):
        card = make_card(FinalStatus.SUPPORTED, candidate_types=[CandidateType.C3_RARE_STABLE])
        assert final_status_ceiling(card).detail["ceiling"] is FinalStatus.SUPPORTED
        no_donors = card.model_copy(
            update={
                "stability_summary": make_stability(
                    results=[
                        result
                        for result in make_stability().results
                        if result.kind is not PerturbationKind.LEAVE_ONE_DONOR
                    ]
                )
            }
        )
        verdict = final_status_ceiling(no_donors)
        assert verdict.detail["candidate_classes"]["missing"]["C3_rare_stable"] == [
            "cross-donor consistency measured"
        ]
        assert verdict.detail["ceiling"] is FinalStatus.PROVISIONAL

    def test_the_queue_cap_is_the_recorded_threshold_not_a_call_site_number(self):
        scores = [(f"card_{index:02d}", 1) for index in range(12)]
        queue, observed = expert_queue(scores)
        assert len(queue) == DEFAULT_THRESHOLDS.expert_queue_card_limit == 10
        assert observed == ["card_10", "card_11"]
        moved = expert_queue(scores, thresholds=DEFAULT_THRESHOLDS.model_copy(update={"expert_queue_card_limit": 2}))
        assert moved[0] == ["card_00", "card_01"]
        with pytest.raises(ValueError, match="room for at least one card"):
            expert_queue(scores, limit=0)
