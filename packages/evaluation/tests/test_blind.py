"""The blind comparison, and everything that makes it not blind (plan V2.0 section 10.7).

A reviewer who can tell which arm came from the platform grades the platform. So the template is fixed down to
the order of its fields and the list of charts it must carry, the roster needs an outsider, the baseline needs
to be two or three traditional workflows run by different people rather than one analyst on one afternoon, and
the scores may only be averaged once alpha clears 0.6. Each of those is a rule about a *derived* reading, so
none of them is stored: ``BlindReview`` holds packets, a roster and scores, and ``reading()`` says what they add
up to.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import evaluation_factories as E
from bioadapt_evaluation.blind import (
    DELIVERY_FIELD_ORDER,
    REQUIRED_FIGURES,
    BlindPacket,
    BlindReview,
    BlindScore,
    Reviewer,
)


class TestTheFixedTemplate:
    def test_a_packet_written_in_another_order_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="not written in the delivery template"):
            BlindPacket(
                packet_id="pkt_x",
                comparison_id=E.COMPARISON,
                fields={"claim": "a", "study_goal": "b"},
                figures=list(REQUIRED_FIGURES),
            )

    def test_a_packet_that_drops_the_unfavourable_chart_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="omits the required figures"):
            E.packet(figures=["evidence_ladder", "per_donor_effect"])

    def test_the_template_is_the_order_the_reader_sees(self) -> None:
        assert tuple(E.packet().fields) == DELIVERY_FIELD_ORDER

    def test_a_model_identifier_left_in_the_prose_is_a_leak(self) -> None:
        made = E.packet(values=[*["neutral"] * 3, "written with flagship-2026-04", *["neutral"] * 4])
        assert made.leaks(["flagship-2026-04"]) == ["flagship-2026-04"]
        assert made.leaks(["nothing_here"]) == []

    def test_leaking_is_case_insensitive_and_reports_each_identity_once(self) -> None:
        made = E.packet(values=["Dr. Okafor"] + ["neutral"] * 7)
        assert made.leaks(["dr. okafor", "dr. okafor"]) == ["dr. okafor"]
        assert made.leaks(["Dr. OKAFOR", "dr. okafor"]) == ["Dr. OKAFOR", "dr. okafor"]


class TestTheRosterAndTheBaseline:
    def test_a_clean_comparison_is_blind_and_comparable(self) -> None:
        reading = E.review().reading(identities=["flagship-2026-04"])
        assert reading.blind
        assert reading.reasons == []
        assert reading.comparable

    def test_two_reviewers_are_not_a_review(self) -> None:
        made = E.review(reviewers=[E.reviewer("reviewer_01"), E.reviewer("reviewer_02")], scores=[])
        assert any("2 reviewers against the floor of 3" in reason for reason in made.reading().reasons)

    def test_a_roster_of_people_who_built_the_platform_reads_its_own_work(self) -> None:
        made = E.review(external=False)
        assert any("nobody is reading the conclusion cold" in reason for reason in made.reading().reasons)

    def test_a_packet_the_whole_roster_did_not_score_is_not_comparable(self) -> None:
        made = E.review(scores=[*E.review().scores[:-1]])
        reading = made.reading()
        assert any("not scored by the whole roster" in reason for reason in reading.reasons)
        assert not reading.comparable

    def test_one_traditional_workflow_is_not_a_baseline(self) -> None:
        made = E.review(baseline_arms=[E.arm("base_1", "analyst_01")])
        assert any("traditional baseline arm(s)" in reason for reason in made.reading().reasons)

    def test_two_arms_by_the_same_analyst_are_one_person_s_day(self) -> None:
        made = E.review(
            baseline_arms=[E.arm("base_1", "analyst_01"), E.arm("base_2", "analyst_01")]
        )
        assert any("share an analyst" in reason for reason in made.reading().reasons)

    def test_a_packet_written_by_a_leaking_hand_is_not_blind(self) -> None:
        made = E.review(
            packets=[
                E.packet(
                    "pkt_a",
                    values=[
                        "neutral",
                        "neutral",
                        "neutral",
                        "the flagship-2026-04 summary says so",
                        "neutral",
                        "neutral",
                        "neutral",
                        "neutral",
                    ],
                ),
                *E.review().packets[1:],
            ]
        )
        reading = made.reading(identities=["flagship-2026-04"])
        assert not reading.blind
        assert reading.leaks == {"pkt_a": ["flagship-2026-04"]}


class TestWhatMayBeAveraged:
    def test_agreement_above_the_floor_aggregates_the_three_dimensions(self) -> None:
        reading = E.review().reading()
        assert reading.agreement is not None and reading.agreement.usable
        aggregate = reading.aggregate("pkt_a")
        assert aggregate["mean_credibility"] == pytest.approx(11 / 3, abs=1e-4)
        assert aggregate["conclusions_to_revise"] == 3.0
        assert aggregate["median_reading_seconds"] == 660.0
        assert aggregate["alpha"] == reading.agreement.alpha

    def test_scores_that_disagree_stay_separate(self) -> None:
        reading = E.review(credibility=E.DISAGREEING).reading()
        assert not reading.comparable
        with pytest.raises(ValueError, match="cannot be aggregated"):
            reading.aggregate("pkt_a")

    def test_a_comparison_with_no_scores_at_all_has_no_agreement_to_check(self) -> None:
        reading = E.review(scores=[]).reading()
        assert reading.agreement is None
        with pytest.raises(ValueError, match="no agreement was measurable"):
            reading.aggregate("pkt_a")

    def test_an_even_roster_gets_a_true_median(self) -> None:
        ids = ["reviewer_01", "reviewer_02", "reviewer_03", "reviewer_04"]
        packets = [E.packet("pkt_a"), E.packet("pkt_b")]
        scores = []
        for position, packet in enumerate(packets):
            for index, name in enumerate(ids):
                scores.append(
                    BlindScore(
                        reviewer_id=name,
                        packet_id=packet.packet_id,
                        credibility=4 + position,
                        conclusions_to_revise=0,
                        reading_seconds=10 * (index + 1) + 10 * position,
                    )
                )
        made = BlindReview(
            comparison_id=E.COMPARISON,
            packets=packets,
            reviewers=[Reviewer(reviewer_id=one, involved_in_development=one != ids[-1]) for one in ids],
            scores=scores,
            baseline_arms=[E.arm("base_1", "analyst_01"), E.arm("base_2", "analyst_02")],
        )
        reading = made.reading()
        assert reading.comparable, reading.reasons
        assert reading.aggregate("pkt_a")["median_reading_seconds"] == 25.0


class TestTheFiledRows:
    def test_a_reviewer_cannot_score_the_same_packet_twice(self) -> None:
        scores = E.review().scores
        with pytest.raises(ValidationError, match="scores a packet once"):
            E.review(scores=[*scores, scores[0]])

    def test_an_unknown_reviewer_cannot_record_a_score(self) -> None:
        with pytest.raises(ValidationError, match="not on the roster"):
            E.review(
                scores=[
                    *E.review().scores,
                    BlindScore(
                        reviewer_id="reviewer_99",
                        packet_id="pkt_a",
                        credibility=4,
                        conclusions_to_revise=0,
                        reading_seconds=600,
                    ),
                ]
            )

    def test_a_score_for_a_packet_outside_the_comparison_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="outside this comparison"):
            E.review(
                scores=[
                    BlindScore(
                        reviewer_id="reviewer_01",
                        packet_id="pkt_other",
                        credibility=4,
                        conclusions_to_revise=0,
                        reading_seconds=600,
                    ),
                ]
            )

    def test_a_packet_from_another_comparison_cannot_join(self) -> None:
        with pytest.raises(ValidationError, match="belongs to"):
            E.review(packets=[*E.review().packets, E.packet("pkt_foreign", "cmp_0002")])

    def test_credibility_is_a_one_to_five_answer(self) -> None:
        with pytest.raises(ValidationError):
            BlindScore(
                reviewer_id="reviewer_01",
                packet_id="pkt_a",
                credibility=6,
                conclusions_to_revise=0,
                reading_seconds=600,
            )
