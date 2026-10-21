"""The expert queue: what a reviewer is shown first, and the two numbers that put it there.

Section 13.4's load model only works if the queue is short and its order is explainable, so the score is
read off the draft and its derived ceiling - never stored - and the cap that splits the queue from the
archive is the plan's own versioned threshold.
"""

from __future__ import annotations

import hashlib
from typing import Optional

import pytest

import evidence_factories as E
from bioadapt_evidence import CardDraft, build_queue, score_draft
from bioadapt_evidence.queue import (
    IMPACT_RARE_AND_GAP,
    IMPACT_RARE_OR_GAP,
    IMPACT_SELF_CONFLICT,
    RULE_VERSION,
    UNCERTAINTY_BY_CEILING,
)
from bioadapt_schemas.entities import CandidateSet
from bioadapt_schemas.enums import (
    CandidateType,
    EvidenceKind,
    EvidencePartition,
    EvidenceRole,
    FieldSource,
    FinalStatus,
    HypothesisType,
    LabelLevel,
    SignOff,
)
from bioadapt_schemas.evidence import (
    CompositionCheck,
    Evidence,
    EvidenceCard,
    EvidenceSource,
    FieldProvenance,
    NoveltyVsCoverage,
)
from bioadapt_schemas.identifiers import derive_candidate
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS


def _cells(name: str) -> list[tuple[str, str]]:
    """A member set unique to one display name, so candidate ids never collide across drafts.

    ``client_ask`` names candidate ids, so a test that asks about one card has to be able to tell the
    queue which card it means - and two drafts over the same cells would be the same candidate.
    """
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()
    barcodes = [digest[index:index + 8] for index in range(0, 16, 8)]
    return [(specimen, barcode) for specimen in E.DISCOVERY_SPECIMENS for barcode in barcodes]


def candidate(name: str) -> CandidateSet:
    candidate_id, fingerprint, size = derive_candidate(_cells(name), E.RUN_ID)
    return CandidateSet(
        candidate_id=candidate_id,
        run_id=E.RUN_ID,
        member_fingerprint=fingerprint,
        size=size,
        donor_count=len(E.DISCOVERY_SPECIMENS),
        display_name=name,
        cluster_origin={"method": "leiden", "resolution": 1.0},
        candidate_types=[CandidateType.C3_RARE_STABLE],
    )


def _record(cand: CandidateSet, *, rare: bool) -> Evidence:
    return Evidence(
        evidence_id=f"ev_composition_{cand.display_name}",
        kind=EvidenceKind.COMPOSITION,
        role=EvidenceRole.TRIGGER,
        partition=EvidencePartition.DISCOVERY,
        source=EvidenceSource(run_id=E.RUN_ID, asset_id="as_clusters"),
        provenance=FieldProvenance(source=FieldSource.TOOL, object_id="as_clusters"),
        independence_group="clusters",
        used_cell_set_fingerprint=cand.member_fingerprint,
        content={
            "size": cand.size,
            "donor_count": cand.donor_count,
            "partition_cells": 2000,
            "share": 0.001 if rare else 0.2,
            "rare": rare,
        },
    )


def _contra(cand: CandidateSet, index: int, judgement: str) -> Evidence:
    return Evidence(
        evidence_id=f"ev_contra_{cand.display_name}_{index}",
        kind=EvidenceKind.STABILITY,
        role=EvidenceRole.CONTRADICTING,
        partition=EvidencePartition.DISCOVERY,
        source=EvidenceSource(run_id=E.RUN_ID, asset_id="as_stability"),
        provenance=FieldProvenance(source=FieldSource.TOOL, object_id="as_stability"),
        independence_group="robustness",
        used_cell_set_fingerprint=cand.member_fingerprint,
        content={"judgement": judgement},
    )


def draft(
    name: str,
    *,
    rare: bool = False,
    gap: Optional[bool] = False,
    ceiling: FinalStatus = FinalStatus.UNKNOWN,
    contradicting: tuple[str, ...] = (),
    reasons: tuple[str, ...] = (),
) -> CardDraft:
    """One draft as the queue sees it: a card carrying its measurements, and the ceiling derived beside it."""
    cand = candidate(name)
    composition = _record(cand, rare=rare)
    evidence = {composition.evidence_id: composition}
    contra_ids: list[str] = []
    for index, judgement in enumerate(contradicting):
        record = _contra(cand, index, judgement)
        evidence[record.evidence_id] = record
        contra_ids.append(record.evidence_id)
    card = EvidenceCard(
        card_id=f"card_{cand.display_name}",
        candidate_id=cand.candidate_id,
        run_id=E.RUN_ID,
        display_name=cand.display_name,
        candidate_types=cand.candidate_types,
        hypothesis_type=HypothesisType.UNKNOWN,
        unknown_reason="insufficient_evidence",
        label_level=LabelLevel.UNKNOWN_COARSE,
        evidence=evidence,
        contradicting_evidence_ids=contra_ids,
        trigger_signal_ids=[composition.evidence_id],
        novelty_vs_coverage=(
            None if gap is None else NoveltyVsCoverage(knn=20, distance_ratio=3.1, coverage_gap=gap)
        ),
        composition_check=CompositionCheck(result="not run"),
        field_provenance={
            "evidence.*.content.*": FieldProvenance(source=FieldSource.TOOL, object_id="as_clusters")
        },
        support_rule_version="support_rule_v1",
        thresholds_version=DEFAULT_THRESHOLDS.version,
        sign_off=SignOff.NOT_SIGNED,
        final_status=FinalStatus.UNKNOWN,
        status_reason="awaiting a person",
    )
    return CardDraft(
        card=card,
        ceiling=ceiling,
        gate_reasons=reasons,
        notes=(),
        support_met=None,
        underpowered_design=False,
    )


class TestScoring:
    def test_a_rare_population_the_reference_does_not_reach_scores_the_highest(self):
        score = score_draft(draft("c002", rare=True, gap=True))
        assert (score.impact, score.uncertainty) == (
            IMPACT_RARE_AND_GAP,
            UNCERTAINTY_BY_CEILING[FinalStatus.UNKNOWN],
        )
        assert score.score == 9
        assert any("rare by the composition rule" in reason for reason in score.reasons)
        assert any("reference does not reach" in reason for reason in score.reasons)

    def test_rarity_or_a_coverage_gap_alone_is_worth_less_than_both(self):
        rare_only = score_draft(draft("c002", rare=True, gap=False))
        gap_only = score_draft(draft("c003", rare=False, gap=True))
        both = score_draft(draft("c004", rare=True, gap=True))
        assert rare_only.impact == gap_only.impact == IMPACT_RARE_OR_GAP
        assert rare_only.score == gap_only.score
        assert both.score > rare_only.score

    def test_a_set_that_is_neither_rare_novel_nor_arguing_with_itself_has_no_impact(self):
        score = score_draft(draft("c000", rare=False, gap=False))
        assert score.impact == 0
        assert score.score == 0
        assert any("not rare by the composition rule" in reason for reason in score.reasons)
        assert any("the reference covers these cells" in reason for reason in score.reasons)

    def test_a_self_conflicting_card_is_worth_one_reading_even_though_it_is_common(self):
        score = score_draft(draft("c001", rare=False, gap=False, contradicting=("fail",)))
        assert score.impact == IMPACT_SELF_CONFLICT
        assert any("argue against" in reason for reason in score.reasons)

    def test_the_ceiling_sets_the_uncertainty_and_is_quoted_back_with_the_gate_reason(self):
        settled = score_draft(
            draft("c002", rare=True, gap=True, ceiling=FinalStatus.REJECTED, reasons=("gate",))
        )
        assert settled.uncertainty == UNCERTAINTY_BY_CEILING[FinalStatus.REJECTED]
        assert any(reason == "ceiling rejected: gate" for reason in settled.reasons)
        open_ended = score_draft(draft("c003", rare=True, gap=True))
        assert any("nothing holds it back" in reason for reason in open_ended.reasons)

    def test_a_card_the_project_named_gets_the_plans_bonus_and_says_so(self):
        score = score_draft(draft("c002", rare=True, gap=True), client_ask=True)
        assert score.score == 9 + 2
        assert any("asking about" in reason for reason in score.reasons)

    def test_a_missing_coverage_measurement_is_reported_as_unknown_not_as_a_gap(self):
        score = score_draft(draft("c002", rare=True, gap=None))
        assert score.impact == IMPACT_RARE_OR_GAP
        assert any("no coverage measurement" in reason for reason in score.reasons)
        assert not any("reference does not reach" in reason for reason in score.reasons)

    def test_a_card_without_a_composition_record_explains_why_nothing_marks_it_rare(self):
        made = draft("c002", rare=True, gap=True)
        card = made.card.model_copy(update={"evidence": {}, "trigger_signal_ids": []})
        score = score_draft(
            CardDraft(
                card=card,
                ceiling=made.ceiling,
                gate_reasons=made.gate_reasons,
                notes=made.notes,
                support_met=None,
                underpowered_design=False,
            )
        )
        assert score.impact == IMPACT_RARE_OR_GAP
        assert any("no composition record" in reason for reason in score.reasons)


class TestQueue:
    def test_the_queue_is_ordered_and_the_rest_is_filed_rather_than_dropped(self):
        drafts = [
            draft("c000", rare=False, gap=False, ceiling=FinalStatus.SUPPORTED),
            draft("c001", rare=True, gap=False),
            draft("c002", rare=True, gap=True),
        ]
        queue = build_queue(drafts, limit=2, thresholds=DEFAULT_THRESHOLDS)
        assert queue.queue == ("card_c002", "card_c001")
        assert queue.observed == ("card_c000",)
        assert queue.limit == 2
        assert queue.rule_version == RULE_VERSION
        assert queue.thresholds_version == DEFAULT_THRESHOLDS.version

    def test_the_cap_defaults_to_the_recorded_threshold_rather_than_a_call_site_number(self):
        drafts = [draft(f"c{index:03d}", rare=True, gap=True) for index in range(12)]
        queue = build_queue(drafts)
        assert len(queue.queue) == DEFAULT_THRESHOLDS.expert_queue_card_limit
        assert len(queue.observed) == 12 - DEFAULT_THRESHOLDS.expert_queue_card_limit

    def test_asking_by_candidate_id_is_what_moves_a_card_up(self):
        drafts = [draft("c001", rare=True, gap=False), draft("c002", rare=True, gap=False)]
        assert drafts[0].candidate_id != drafts[1].candidate_id
        queue = build_queue(drafts, client_ask=[drafts[1].candidate_id])
        assert queue.queue[0] == drafts[1].card_id
        assert queue.score_of(drafts[1].card_id).client_ask is True
        assert queue.score_of(drafts[0].card_id).client_ask is False

    def test_the_same_drafts_always_give_the_same_queue(self):
        drafts = [draft("c003", rare=True, gap=True), draft("c001", rare=False, gap=True), draft("c002")]
        first = build_queue(drafts, limit=1)
        second = build_queue(list(reversed(drafts)), limit=1)
        assert first.queue == second.queue
        assert first.observed == second.observed

    def test_the_score_of_a_card_not_in_the_queue_is_a_lookup_error(self):
        queue = build_queue([draft("c001")])
        with pytest.raises(LookupError, match="not in this queue"):
            queue.score_of("card_missing")

    def test_the_envelope_carries_both_numbers_for_every_card(self):
        drafts = [draft("c001", rare=True, gap=True), draft("c002", rare=False, gap=False)]
        body = build_queue(drafts, limit=1).as_dict()
        assert {row["card_id"] for row in body["scores"]} == {"card_c001", "card_c002"}
        assert all({"impact", "uncertainty", "score", "reasons"} <= set(row) for row in body["scores"])
        assert body["scores"][0]["score"] > body["scores"][1]["score"]
        assert body["queue"] == ["card_c001"]
        assert body["observed"] == ["card_c002"]
