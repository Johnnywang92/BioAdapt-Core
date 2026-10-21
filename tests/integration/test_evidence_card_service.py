"""A finished run, read back as evidence cards (plan V2.0 sections 4.1, 5.2, 5.9, 7.2-7.8, 9.1, 13.4).

The asset contract has two halves written by different code: the workflow's product classes name what a
node emitted, and ``bioadapt_evidence`` restates those names on the consumer side because §9.5 keeps the
layers apart. A unit test can only prove each half agrees with itself. This file runs the reference chain
for real and proves the two halves still name the same slots, the same criteria and the same partitions -
which is where a card would otherwise cite a number the run never measured.

No assertion here hard-codes a verdict. Each expected value is read back out of a stored product or out of
the pinned plan, so a change in what the workflow measures moves these tests instead of hiding behind them.
"""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import timedelta
from typing import Any, Sequence

import pytest

import driver_factories as F
from bioadapt_evidence import (
    CARD_OUTBOUND,
    INTERMEDIATE_SLOTS,
    SCHEMA_BY_SLOT,
    AssetReader,
    CardDraft,
    CardUnderReview,
    ContractDrift,
    EgressBlocked,
    EvidenceCardService,
    MissingProduct,
    build_queue,
    card_outbound,
    specimen_of,
)
from bioadapt_evidence.assets import (
    SLOT_CLUSTERS,
    SLOT_COVERAGE,
    SLOT_DE_TABLE,
    SLOT_EXPRESSION,
    SLOT_MARKERS,
    SLOT_QC_FLAGS,
    SLOT_STABILITY,
    SLOT_STATE_AXIS,
)
from bioadapt_policies.egress import (
    EgressApproval,
    EgressRequest,
    OutgoingField,
    build_outbound_context,
    evaluate_egress,
)
from bioadapt_schemas.enums import (
    ArtifactRisk,
    EvidenceKind,
    EvidencePartition,
    EvidenceRole,
    FieldSource,
    FinalStatus,
    HypothesisType,
    LabelLevel,
    PrivacyTier,
    ScreeningResult,
    SignOff,
)
from bioadapt_schemas.gates import numeric_provenance_violations
from bioadapt_schemas.identifiers import CELL_KEY_SEPARATOR
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS, Thresholds
from bioadapt_scrnaseq import PRODUCT_SCHEMAS, SLOT_COUNTS

#: The slots a finished follow-up run leaves on the wires, split by whether a card may cite an answer from
#: them. ``features``, ``embedding`` and ``graph`` are fetched, digest-checked and set aside (§5.9).
CITABLE_SLOTS = (
    SLOT_CLUSTERS,
    SLOT_COUNTS,
    SLOT_COVERAGE,
    SLOT_DE_TABLE,
    SLOT_EXPRESSION,
    SLOT_MARKERS,
    SLOT_QC_FLAGS,
    SLOT_STABILITY,
    SLOT_STATE_AXIS,
)
INTERMEDIATE_ONLY = ("embedding", "features", "graph")

FOCUS = F.CANDIDATE_CLUSTER

#: A model endpoint inside the data plane: §11.5's field allowlist, not the network, is what these tests
#: are about, so the destination is one the routing layer would accept and only the fields can refuse.
LOCAL_ENDPOINT = "https://model.internal"
PURPOSE = "describe_a_candidate_to_a_model"


def egress_request(fields: Sequence[OutgoingField], tier: PrivacyTier) -> EgressRequest:
    return EgressRequest(
        project_id=F.PROJECT_ID,
        privacy_tier=tier,
        purpose=PURPOSE,
        destination=LOCAL_ENDPOINT,
        fields=list(fields),
    )


def egress_approval(project_id: str) -> EgressApproval:
    return EgressApproval(
        approval_id="appr_card_outbound",
        approver_id=F.PI,
        project_id=project_id,
        purpose=PURPOSE,
        approved_on=F.NOW.date() - timedelta(days=1),
        expires_on=F.NOW.date() + timedelta(days=30),
    )


def evidence_of(card: Any, kind: str) -> Any:
    """The one record a card carries of a kind that answers one question; a class with several arms is not
    looked up this way."""
    found = [entry for entry in card.evidence.values() if entry.kind == kind]
    assert len(found) == 1, f"{card.display_name}: {kind} appears {len(found)} times"
    return found[0]


class TestContractParity:
    """The two halves of §4.1: the names the writer declares and the names the reader expects."""

    def test_every_slot_the_run_wrote_is_a_contract_the_card_reader_names_by_the_same_name(self, carded):
        reader = carded.service.reader
        for slot in carded.assets:
            asset = carded.asset(slot)
            expected = SCHEMA_BY_SLOT.get(slot) or INTERMEDIATE_SLOTS[slot]
            assert expected == asset.schema_version, f"{slot}: reader {expected} writer {asset.schema_version}"
            assert carded.measured(slot).slot == slot, "the bytes name the slot the index row was filed under"
        assert set(reader.slots()) == set(CITABLE_SLOTS) <= set(carded.assets)

    def test_the_reader_names_no_contract_the_workflow_cannot_write(self, carded):
        for schema in [*SCHEMA_BY_SLOT.values(), *INTERMEDIATE_SLOTS.values()]:
            assert schema in PRODUCT_SCHEMAS, f"{schema} is a contract only the reader believes in"

    def test_an_intermediate_is_verified_but_answers_no_slot_a_card_can_cite(self, carded):
        reader = carded.service.reader
        assert reader.intermediate_slots() == INTERMEDIATE_ONLY
        assert set(reader.slots()) | set(reader.intermediate_slots()) == set(carded.assets)
        assert reader.optional(SLOT_EXPRESSION) is not None
        for slot in INTERMEDIATE_ONLY:
            with pytest.raises(MissingProduct, match=f"produced no {slot!r}"):
                reader.product(slot)

    def test_a_run_answers_its_counts_slot_and_the_upload_is_only_verified(self, harness_factory):
        """§5.2: the QC step's filtered matrix is the answer; the upload is read, checked, then set aside."""
        h = harness_factory(the_plan=F.plan())
        out = h.run()
        reader = AssetReader(h.catalog, h.objects, run_id=h.run_id)
        assert reader.product(SLOT_COUNTS).asset_id == out.assets[SLOT_COUNTS]
        assert reader.product(SLOT_COUNTS).asset_id != h.root[SLOT_COUNTS]

    def test_an_upload_whose_bytes_are_gone_still_stops_the_card(self, harness_factory):
        h = harness_factory(the_plan=F.plan())
        h.run()
        upload = h.root[SLOT_COUNTS]
        h.objects.delete(h.asset(upload).uri[len("s3://") :])
        with pytest.raises(MissingProduct, match=upload):
            AssetReader(h.catalog, h.objects, run_id=h.run_id)

    def test_a_caller_can_still_ask_for_the_uploaded_matrix_instead(self, harness_factory):
        """The one case naming an asset has to resolve: two rows, one slot, a person decides which to read."""
        h = harness_factory(the_plan=F.plan())
        out = h.run()
        reader = AssetReader(h.catalog, h.objects, run_id=h.run_id, extra_asset_ids=[h.root[SLOT_COUNTS]])
        assert reader.product(SLOT_COUNTS).asset_id == h.root[SLOT_COUNTS]
        assert reader.product(SLOT_COUNTS).asset_id != out.assets[SLOT_COUNTS]


class TestDraftingRealProducts:
    """What the measurements say, when a card - not a driver - is the one reading them."""

    def test_a_card_is_drafted_for_every_candidate_the_index_holds_in_a_stable_order(self, carded):
        clusters = carded.measured(SLOT_CLUSTERS)
        assert len(carded.drafts) == len(carded.candidates) == len(clusters.clusters)
        assert [d.card.candidate_id for d in carded.drafts] == sorted(c.candidate_id for c in carded.candidates)
        #: The order is the id's, so it is not the display name's - which here is a different sequence.
        assert [d.card.display_name for d in carded.drafts] != sorted(d.card.display_name for d in carded.drafts)
        assert [d.card_id for d in carded.service.drafts()] == [d.card_id for d in carded.drafts]

    def test_a_draft_files_an_unknown_hypothesis_and_shows_the_ceiling_beside_the_card(self, carded):
        for draft in carded.drafts:
            card = draft.card
            assert card.hypothesis_type is HypothesisType.UNKNOWN
            assert card.final_status is FinalStatus.UNKNOWN
            assert card.label_level is LabelLevel.UNKNOWN_COARSE
            assert (card.candidate_label, card.ontology.term_id, card.reviewer_id) == (None, None, None)
            assert card.sign_off is SignOff.NOT_SIGNED
            #: The ceiling is a returned reading, never a field that outlives the numbers behind it.
            assert draft.ceiling is not card.final_status

    def test_no_card_of_this_run_may_read_as_supported_for_no_measurement_reaches_the_confirmation_partition(
        self, carded
    ):
        partitions = F.plan().data_partitions
        for draft in carded.drafts:
            card = draft.card
            assert {e.partition for e in card.evidence.values()} == {EvidencePartition.DISCOVERY}
            assert card.confirmation_evidence_ids == []
            assert draft.ceiling is FinalStatus.PROVISIONAL
            assert "no independent confirmation evidence" in draft.gate_reasons
            #: And the reason is the plan's own specimen rule applied to the cells the tool measured.
            cells = dict(carded.measured(SLOT_CLUSTERS).cells_by_cluster())[card.display_name]
            specimens = {specimen_of(key) for key in cells}
            assert specimens & set(partitions.discovery_specimens)
            assert specimens & set(partitions.confirmation_specimens), "a shared set confirms nothing (§7.4)"

    def test_a_stability_answer_on_a_card_is_the_robustness_products_own_verdict_of_the_same_rows(
        self, carded
    ):
        """Reader-side §7.5 against writer-side §7.5, over the arms one candidate was measured with."""
        product = carded.measured(SLOT_STABILITY)
        narrowed = replace(product, rows=tuple(r for r in product.rows if r.baseline_cluster == FOCUS))
        results = {r.kind.value: r for r in carded.draft(FOCUS).card.stability_summary.results}
        assert set(results) == set(narrowed.kinds)
        for kind, result in results.items():
            assert result.detail["judgement"] == narrowed.judgement(kind), kind
            assert result.replicates == len(narrowed.rows_for(kind)), kind
        #: Every arm met its recorded criteria, and the card is still not supported: §7.5's verdict is one
        #: input to the gate, not the answer the reviewer came for.
        assert narrowed.verdict() == "stable"
        assert carded.draft(FOCUS).ceiling is FinalStatus.PROVISIONAL

    def test_a_candidate_the_robustness_node_was_not_pointed_at_has_no_stability_answer(self, carded):
        quiet = carded.unmeasured()
        draft = carded.draft(quiet)
        assert draft.card.stability_summary is None
        assert "no stability summary" in draft.gate_reasons
        assert carded.asset(SLOT_STABILITY).asset_id in " ".join(draft.notes)
        assert "measured no perturbation arm" in " ".join(draft.notes)

    def test_a_coverage_number_on_a_card_is_the_stored_measurement_and_never_a_novelty_claim(self, carded):
        coverage = carded.measured(SLOT_COVERAGE)
        novelty = carded.draft(FOCUS).card.novelty_vs_coverage
        assert novelty.knn == int(coverage.criteria["knn"])
        assert novelty.distance_ratio == pytest.approx(coverage.distance_ratio)
        assert novelty.coverage_gap is coverage.coverage_gap
        assert coverage.coverage_gap is (
            coverage.distance_ratio >= coverage.criteria["distance_ratio_min"]
        ), "the card cannot hold a gap the stored numbers do not give"
        #: §7.6 keeps the two claims apart: no score exists for this product, so none is invented, and a
        #: reference that does reach these cells argues against the population being new.
        assert novelty.reference_coverage_score is None
        assert "No coverage score is defined" in novelty.note
        assert evidence_of(carded.draft(FOCUS).card, "reference_coverage").role is EvidenceRole.CONTRADICTING

    def test_the_axis_question_is_answered_only_for_the_candidate_the_axis_was_drawn_over(self, carded):
        axis = carded.measured(SLOT_STATE_AXIS)
        measured = [
            name
            for name, cells in carded.measured(SLOT_CLUSTERS).cells_by_cluster().items()
            if {row.cell_key for row in axis.rows} <= set(cells)
        ]
        assert measured == ["c001"], "the plan pointed this node at one named candidate, not at the partition"
        for name in measured:
            check = carded.draft(name).card.identity_vs_state.continuous_state_check
            assert check.method == axis.method
            assert check.is_axis_tail is axis.is_axis_tail
            assert check.statistic == pytest.approx(axis.tail_share, abs=1e-6)
        for draft in carded.drafts:
            if draft.card.display_name in measured:
                continue
            assert draft.card.identity_vs_state.continuous_state_check is None
            assert "measured no cell of" in " ".join(draft.notes)

    def test_an_artefact_risk_is_credited_to_a_tool_or_left_explicitly_unscreened(self, carded):
        screening = carded.draft(FOCUS).card.artifact_screening
        assert screening.blocked_by == []
        assert screening.not_screened == [ArtifactRisk.STRESS_CYCLE]
        stress = screening.items[ArtifactRisk.STRESS_CYCLE]
        assert (stress.tool, stress.tool_version, stress.threshold_source) == (None, None, None)
        assert "unscreened rather than assumed clear" in stress.detail
        qc_asset = carded.asset(SLOT_QC_FLAGS).asset_id
        for risk in (ArtifactRisk.DOUBLET, ArtifactRisk.AMBIENT):
            item = screening.items[risk]
            assert item.result is ScreeningResult.PASS
            assert item.tool_version == carded.service.reader.tool_of(qc_asset)[2]
        #: The two partition risks are read from perturbation arms, and the card labels that as a proxy.
        stability_asset = carded.asset(SLOT_STABILITY).asset_id
        for risk, kind in ((ArtifactRisk.BATCH_SPECIFIC, "leave_one_donor"), (ArtifactRisk.OVER_SPLIT, "resolution")):
            item = screening.items[risk]
            assert item.result is ScreeningResult.PASS and "proxy" in item.detail
            assert item.tool == carded.service.reader.tool_of(stability_asset)[1]

    def test_a_risk_a_candidate_was_never_measured_for_is_not_screened_and_says_which_product_lacks_it(
        self, carded
    ):
        quiet = carded.draft(carded.unmeasured()).card.artifact_screening
        for risk in (ArtifactRisk.BATCH_SPECIFIC, ArtifactRisk.OVER_SPLIT):
            item = quiet.items[risk]
            assert item.result is ScreeningResult.NOT_RUN and item.tool is None
            assert carded.asset(SLOT_STABILITY).asset_id in item.detail
            assert "measured no" in item.detail, "the run stored a robustness product; it just did not measure this set"

    def test_the_marker_support_question_stays_unanswered_when_the_run_measured_no_cluster_contrast(
        self, carded
    ):
        table = carded.measured(SLOT_DE_TABLE)
        assert {str(detail["arms"]) for detail in table.contrast_details.values()} == {"condition"}
        assert all(draft.support_met is None for draft in carded.drafts)
        assert all(not draft.card.candidate_label for draft in carded.drafts)

    def test_every_number_on_a_real_card_names_the_asset_that_measured_it(self, carded):
        pinned = {node.tool_name: node.tool_version for node in carded.service.plan.nodes}
        for draft in carded.drafts:
            card = draft.card
            assert numeric_provenance_violations(card) == []
            assert len(card.field_provenance) > 0
            for entry in card.evidence.values():
                assert entry.provenance.source is FieldSource.TOOL
                assert entry.provenance.object_id == entry.source.asset_id
                _node_id, tool, version = carded.service.reader.tool_of(entry.source.asset_id)
                assert pinned[tool] == version, f"{card.display_name}: {tool} is not the pinned version"


class TestDeterminismAndFiling:
    """§9.1: the drafting path may file a card, and may not decide anything with it."""

    def test_the_same_bytes_draft_the_same_card(self, carded):
        def dumped(drafts: Sequence[CardDraft]) -> list[str]:
            return [json.dumps(d.card.model_dump(mode="json"), sort_keys=True) for d in drafts]

        again = EvidenceCardService(
            catalog=carded.harness.catalog, objects=carded.harness.objects, run_id=carded.harness.run_id
        ).drafts()
        assert dumped(again) == dumped(carded.drafts)

    def test_filing_a_draft_records_the_card_as_drafted_and_leaves_it_unknown(self, carded):
        catalog = carded.harness.catalog
        for draft in carded.drafts:
            carded.service.persist(draft)
            filed = catalog.get_card(draft.card_id)
            assert filed.model_dump(mode="json") == draft.card.model_dump(mode="json")
            assert catalog.card_final_status(draft.card_id) == FinalStatus.UNKNOWN.value

    def test_a_card_somebody_has_acted_on_is_not_rewritten_by_a_rebuild(self, carded):
        catalog = carded.harness.catalog
        draft = carded.draft(FOCUS)
        catalog.update_card(
            catalog.get_card(draft.card_id).model_copy(
                update={"final_status": FinalStatus.REJECTED, "reviewer_id": F.PI, "review_reason": "no"}
            )
        )
        with pytest.raises(CardUnderReview, match="cannot overwrite a card somebody has acted on"):
            carded.service.persist(carded.service.draft(carded.candidate(FOCUS)))
        assert catalog.get_card(draft.card_id).final_status is FinalStatus.REJECTED

    def test_a_service_handed_a_revised_threshold_set_refuses_before_it_can_file(self, carded):
        cited = carded.measured(SLOT_STABILITY).thresholds_version
        assert cited == DEFAULT_THRESHOLDS.version
        with pytest.raises(ContractDrift, match="in force when they were measured"):
            EvidenceCardService(
                catalog=carded.harness.catalog,
                objects=carded.harness.objects,
                run_id=carded.harness.run_id,
                thresholds=Thresholds(version="v1-measured", confirmed=True),
            )


class TestQueueOverRealCards:
    """§7.2's promise: the queue is short, and the reason it is ordered this way is written down."""

    def test_the_queue_holds_every_card_of_a_run_under_the_cap_and_reasons_each_place(self, carded):
        built = build_queue(carded.drafts)
        assert built.limit == DEFAULT_THRESHOLDS.expert_queue_card_limit
        assert len(carded.drafts) < built.limit
        assert built.observed == ()
        scores = {s.card_id: s for s in built.scores}
        assert built.queue == tuple(sorted(scores, key=lambda card_id: (-scores[card_id].score, card_id)))
        cells = len(carded.measured(SLOT_CLUSTERS).labels)
        for score in built.scores:
            card = carded.draft(score.display_name).card
            assert score.reasons, score.display_name
            assert f"{carded.candidate(score.display_name).size} of {cells} cells" in score.reasons[0]
            assert score.impact == (1 if card.contradicting_evidence_ids else 0), "impact reads the card, not a claim"
        assert {s.uncertainty for s in built.scores} == {2}, "every ceiling here is provisional"

    def test_the_projects_own_question_moves_a_card_without_inventing_evidence(self, carded):
        quiet = carded.draft(carded.unmeasured())
        before = build_queue(carded.drafts)
        after = build_queue(carded.drafts, client_ask=[quiet.candidate_id])
        by_id = {s.card_id: s for s in after.scores}
        asked = by_id[quiet.card_id]
        was = next(s for s in before.scores if s.card_id == quiet.card_id)
        assert asked.score == was.score + 2
        assert (asked.impact, asked.uncertainty) == (was.impact, was.uncertainty)
        assert "the project named this population" in asked.reasons[-1]
        assert set(after.queue) == set(before.queue)


class TestCardOutboundFace:
    """§11.5 read against a real card: the outbound context is built from a list of fields, and everything
    the run measured about a cell or a person stays on this side of the list."""

    def test_the_face_carries_no_cell_no_specimen_and_no_object_address(self, carded):
        by_cluster = carded.measured(SLOT_CLUSTERS).cells_by_cluster()
        cells = [key for keys in by_cluster.values() for key in keys]
        specimens = sorted({specimen_of(key) for key in cells})
        rows = carded.harness.catalog.list_assets(run_id=carded.harness.run_id)
        addresses = sorted(
            {a.asset_id for a in rows} | {a.file_sha256 for a in rows} | {a.content_fingerprint for a in rows}
        )
        fingerprints = sorted({c.member_fingerprint for c in carded.candidates})
        assert cells and specimens and addresses and fingerprints, "the run left nothing that could leak"
        for draft in carded.drafts:
            text = json.dumps(card_outbound(draft)[0], sort_keys=True, default=str)
            assert CELL_KEY_SEPARATOR not in text
            for forbidden in cells + specimens + addresses + fingerprints:
                assert forbidden not in text, f"{draft.card.display_name}: {forbidden} left the data plane"

    def test_the_projection_is_the_cards_own_group_level_answer(self, carded):
        """The face repeats what the card already says; it does not measure anything a second time."""
        draft = carded.draft(FOCUS)
        card = draft.card
        template = card_outbound(draft)[0]
        assert template["candidate"] == card.display_name
        assert template["ceiling"] == draft.ceiling.value
        assert template["gate_reasons"] == list(draft.gate_reasons)
        assert template["composition"] == evidence_of(card, EvidenceKind.COMPOSITION).content
        assert template["markers"] == evidence_of(card, EvidenceKind.MARKER_SPECIFICITY).content
        assert template["rule_versions"] == {
            "support_rule_version": card.support_rule_version,
            "thresholds_version": card.thresholds_version,
        }

    def test_a_reference_answer_reaches_the_face_only_for_the_population_it_measured(self, carded):
        coverage = carded.measured(SLOT_COVERAGE)
        card = carded.draft(FOCUS).card
        measured = card_outbound(carded.draft(FOCUS))[0]["reference_coverage"]
        assert measured["knn"] == int(coverage.criteria["knn"])
        assert measured["distance_ratio"] == card.novelty_vs_coverage.distance_ratio
        assert measured["coverage_gap"] is card.novelty_vs_coverage.coverage_gap
        assert card_outbound(carded.draft(carded.unmeasured()))[0]["reference_coverage"] == {}

    def test_a_call_site_may_narrow_the_face_but_never_widen_it(self, carded):
        draft = carded.draft(FOCUS)
        template, fields = card_outbound(draft, only=["candidate", "markers"])
        assert sorted(template) == ["candidate", "markers"]
        assert [declared.path for declared in fields] == ["candidate", "markers"]
        with pytest.raises(EgressBlocked) as raised:
            card_outbound(draft, only=["candidate", "member_fingerprint"])
        assert "§11.5" in raised.value.message

    def test_the_project_sends_the_face_only_under_its_own_tiers_rules(self, carded):
        """The tier the study filed at decides whether any of it may leave, not the caller."""
        template, fields = card_outbound(carded.draft(FOCUS))
        project = carded.harness.catalog.get_project()
        unapproved = evaluate_egress(
            egress_request(fields, project.privacy_tier),
            today=F.NOW.date(),
            local_hosts={LOCAL_ENDPOINT},
        )
        assert unapproved.requires_approval
        assert not unapproved.allowed
        assert any("approval" in reason for reason in unapproved.blocked_reasons)
        cleared = evaluate_egress(
            egress_request(fields, project.privacy_tier),
            today=F.NOW.date(),
            local_hosts={LOCAL_ENDPOINT},
            approval=egress_approval(project.project_id),
        )
        assert cleared.allowed, cleared.blocked_reasons
        assert build_outbound_context(template, cleared) == template

    @pytest.mark.parametrize(
        "tier", [PrivacyTier.STRICT_LOCAL, PrivacyTier.HGR_CONTROLLED], ids=lambda t: t.value
    )
    def test_a_local_only_tier_sends_this_run_nothing(self, carded, tier: PrivacyTier) -> None:
        template, fields = card_outbound(carded.draft(FOCUS))
        decision = evaluate_egress(
            egress_request(fields, tier),
            today=F.NOW.date(),
            local_hosts={LOCAL_ENDPOINT},
            approval=egress_approval(F.PROJECT_ID),
        )
        assert not decision.allowed
        assert decision.allowed_paths == []
        assert len(decision.denials) == len(CARD_OUTBOUND)
        with pytest.raises(ValueError):
            build_outbound_context(template, decision)
