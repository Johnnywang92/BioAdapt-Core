"""Krippendorff's alpha, and the floor below which two reviewers are not one number (plan V2.0 10.7).

Section 10.7 makes aggregation conditional: a comparison may be averaged only once alpha reaches 0.6. The point
of computing it here rather than storing it is that the number is a property of the scores, not of the report -
so a roster that disagreed says so every time it is read. An alpha of ``None`` is a different finding from an
alpha of 0.31: the first means there was nothing to measure, the second means the reviewers could not agree,
and only the second is the interesting result.
"""

from __future__ import annotations

import pytest

from bioadapt_evaluation.agreement import Agreement, AgreementLevel, krippendorff


def interval(*rows: dict[str, int | None], **overrides) -> Agreement:
    return krippendorff(list(rows), level=AgreementLevel.INTERVAL, **overrides)


class TestWhatAlphaMeasures:
    def test_reviewers_who_never_differ_within_a_packet_agree_perfectly(self) -> None:
        made = interval(
            {"r1": 4, "r2": 4, "r3": 4},
            {"r1": 2, "r2": 2, "r3": 2},
            {"r1": 5, "r2": 5, "r3": 5},
        )
        assert made.alpha == 1.0
        assert made.usable

    def test_a_spread_of_opinions_is_far_below_the_floor(self) -> None:
        made = interval({"r1": 1, "r2": 5, "r3": 3}, {"r1": 5, "r2": 1, "r3": 4})
        assert made.alpha is not None and made.alpha < 0.6
        assert not made.usable
        assert any("below the 0.6 floor" in reason for reason in made.reasons)

    def test_scores_worse_than_chance_are_reported_as_negative_not_clipped(self) -> None:
        made = interval({"r1": 1, "r2": 5}, {"r1": 5, "r2": 1}, {"r1": 1, "r2": 5}, {"r1": 5, "r2": 1})
        assert made.alpha is not None and made.alpha < 0

    def test_the_floor_travels_with_the_measurement(self) -> None:
        rows = [
            {"r1": 4, "r2": 4, "r3": 3},
            {"r1": 5, "r2": 5, "r3": 5},
            {"r1": 2, "r2": 2, "r3": 3},
        ]
        at_plan_floor = krippendorff(rows, level=AgreementLevel.INTERVAL)
        assert at_plan_floor.threshold == 0.6
        assert at_plan_floor.usable
        stricter = krippendorff(rows, level=AgreementLevel.INTERVAL, threshold=0.99)
        assert stricter.alpha == at_plan_floor.alpha
        assert not stricter.usable


class TestWhenThereIsNoNumber:
    def test_identical_answers_everywhere_leave_nothing_to_measure(self) -> None:
        made = interval({"r1": 4, "r2": 4, "r3": 4}, {"r1": 4, "r2": 4, "r3": 4})
        assert made.alpha is None
        assert any("unreported rather than assumed perfect" in reason for reason in made.reasons)
        assert not made.usable

    def test_one_packet_is_not_a_reliability_study(self) -> None:
        made = interval({"r1": 4, "r2": 3})
        assert made.alpha is None
        assert any("at least two units" in reason for reason in made.reasons)

    def test_one_reviewer_cannot_agree_with_anybody(self) -> None:
        made = interval({"r1": 4}, {"r1": 2})
        assert made.alpha is None

    def test_a_packet_with_one_answer_contributes_no_pair(self) -> None:
        made = interval(
            {"r1": 4, "r2": 4, "r3": None},
            {"r1": 2, "r2": 2, "r3": None},
            {"r1": 5, "r2": None, "r3": None},
        )
        assert made.units == 2
        assert any("fewer than two answers" in reason for reason in made.reasons)

    def test_the_counted_units_are_the_repliable_ones(self) -> None:
        made = interval({"r1": 4, "r2": 4}, {"r1": 2, "r2": 2})
        assert made.units == 2
        assert made.observers == 2


class TestLevels:
    def test_nominal_scores_measure_category_mismatch_not_distance(self) -> None:
        close = krippendorff(
            [{"r1": "a", "r2": "a"}, {"r1": "b", "r2": "b"}, {"r1": "a", "r2": "a"}],
            level=AgreementLevel.NOMINAL,
        )
        assert close.alpha == 1.0

    def test_the_same_scores_at_the_two_levels_are_not_the_same_number(self) -> None:
        """At the interval level a near miss costs less than a far one, which nominal scoring cannot see."""
        rows = [{"r1": 1, "r2": 1, "r3": 2}, {"r1": 5, "r2": 5, "r3": 4}]
        nominal = krippendorff(rows, level=AgreementLevel.NOMINAL)
        interval_level = krippendorff(rows, level=AgreementLevel.INTERVAL)
        assert nominal.alpha is not None and interval_level.alpha is not None
        assert nominal.alpha != pytest.approx(interval_level.alpha)
