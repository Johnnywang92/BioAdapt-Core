"""The metric dictionary and the ways its numbers are made to say something false (10.4, 10.6).

Section 10.6 does not stop at fourteen rows; its last column names, for each row, the value that would be a lie
- an interval that covers the baseline reported as an improvement, a risk-coverage curve bought by labelling
everything coarsely, byte-level determinism standing in for a GPU's numeric noise, a total elapsed time with
the queue wait folded into it. Those are the parts worth testing, because a scorer that only compares a number
to a target passes every one of them. The dictionary holds them as data and one evaluator applies them, so a
new row cannot quietly leave its own cheating rule out.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

import evaluation_factories as E
from bioadapt_evaluation.metrics import (
    METRIC_DICTIONARY,
    CollectionPoint,
    Direction,
    MetricDefinition,
    MetricId,
    MetricStatus,
    dictionary_row,
    evaluate_all,
    evaluate_metric,
)


def verdict(metric_id: MetricId, **overrides) -> MetricStatus:
    fields = dict(E.PASSING[metric_id])
    fields.update(overrides)
    return evaluate_metric(dictionary_row(metric_id), E.observation(metric_id, **fields)).status


class TestTheDictionaryItself:
    def test_section_10_6_has_fourteen_rows_and_all_are_measurable(self) -> None:
        assert len(METRIC_DICTIONARY) == 14
        assert set(METRIC_DICTIONARY) == set(MetricId)

    def test_every_row_can_be_measured_and_pass(self) -> None:
        """A row nobody could satisfy would gate a release that never happens."""
        for metric_id in METRIC_DICTIONARY:
            made = evaluate_metric(dictionary_row(metric_id), E.passing_observation(metric_id))
            assert made.status is MetricStatus.PASS, (metric_id, made.reasons)
            assert not made.release_blocking

    def test_every_row_states_its_own_denominator(self) -> None:
        for metric_id, definition in METRIC_DICTIONARY.items():
            assert definition.denominator, metric_id
            assert definition.numerator, metric_id
            assert definition.collection_point in set(CollectionPoint)
            assert "§10.6" in definition.unacceptable or "§10.8" in definition.unacceptable

    def test_a_row_that_compares_cannot_also_name_a_single_number(self) -> None:
        """Section 10.4 forbids a single-number gate before a baseline exists; the table cannot smuggle one."""
        with pytest.raises(ValidationError, match="names a single number"):
            MetricDefinition(
                metric_id=MetricId.RARE_CLASS_RECALL,
                numerator="recovered",
                denominator="pre-labelled",
                collection_point=CollectionPoint.STAGE5_EVALUATION,
                direction=Direction.ABOVE_BASELINE,
                baseline_required=True,
                target=0.7,
                unacceptable="a threshold invented before the baseline",
            )

    def test_a_comparison_must_require_a_baseline(self) -> None:
        with pytest.raises(ValidationError, match="does not require one"):
            MetricDefinition(
                metric_id=MetricId.SUPPORT_VALIDITY,
                numerator="supported",
                denominator="cited",
                collection_point=CollectionPoint.L5_VERIFICATION,
                direction=Direction.BELOW_BASELINE,
                unacceptable="a comparison with nothing to compare to",
            )

    def test_a_direction_without_a_number_is_refused(self) -> None:
        with pytest.raises(ValidationError, match="without a number"):
            MetricDefinition(
                metric_id=MetricId.CALIBRATION,
                numerator="expected calibration error",
                denominator="groups scored",
                collection_point=CollectionPoint.EVALUATION,
                direction=Direction.BELOW,
                unacceptable="an unmeasured threshold",
            )

    def test_a_row_describes_its_own_measurement(self) -> None:
        assert "expert pre-labelled candidates" in dictionary_row(
            MetricId.RARE_CLASS_RECALL
        ).describe()


class TestTheCheatingRules:
    def test_an_interval_that_covers_the_baseline_is_not_an_improvement(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.RARE_CLASS_RECALL),
            E.observation(
                MetricId.RARE_CLASS_RECALL,
                numerator=45,
                denominator=60,
                baseline=0.6,
                interval=(0.55, 0.80),
            ),
        )
        assert made.status is MetricStatus.FAIL
        assert any("covers the baseline" in reason for reason in made.reasons)
        assert not made.release_blocking

    def test_the_same_interval_without_an_improvement_is_only_a_trend(self) -> None:
        assert (
            verdict(
                MetricId.RARE_CLASS_RECALL, numerator=35, denominator=60, baseline=0.6, interval=(0.5, 0.7)
            )
            is MetricStatus.TREND_ONLY
        )

    def test_no_baseline_means_no_gate(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.ABSTENTION_QUALITY),
            E.observation(MetricId.ABSTENTION_QUALITY, numerator=9, denominator=100),
        )
        assert made.status is MetricStatus.TREND_ONLY
        assert any("section 10.4 forbids" in reason for reason in made.reasons)

    def test_a_comparison_without_an_interval_is_reported_not_gated(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.RARE_CLASS_RECALL),
            E.observation(MetricId.RARE_CLASS_RECALL, numerator=45, denominator=60, baseline=0.6),
        )
        assert made.status is MetricStatus.TREND_ONLY
        assert any("bootstrap interval" in reason for reason in made.reasons)

    def test_a_latency_total_without_its_waits_fails(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.END_TO_END_LATENCY),
            E.observation(MetricId.END_TO_END_LATENCY, value=3000.0, segments={"platform_compute": 3000.0}),
        )
        assert made.status is MetricStatus.FAIL
        assert any("queue_wait" in reason and "review_wait" in reason for reason in made.reasons)

    def test_byte_determinism_cannot_stand_in_for_a_gpu(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.COMPUTE_REPRODUCIBILITY),
            E.observation(
                MetricId.COMPUTE_REPRODUCIBILITY, value=1.0, levels={"byte": 1.0, "numeric": 1.0}
            ),
        )
        assert made.status is MetricStatus.FAIL
        assert any("conclusion" in reason for reason in made.reasons)

    def test_coverage_bought_with_coarse_labels_fails_its_companion_check(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.ABSTENTION_QUALITY),
            E.observation(
                MetricId.ABSTENTION_QUALITY,
                numerator=9,
                denominator=100,
                baseline=0.2,
                interval=(0.05, 0.13),
                companions={"coarse_label_share": 0.55},
                companion_baselines={"coarse_label_share": 0.10},
            ),
        )
        assert made.status is MetricStatus.FAIL
        assert any("coarse_label_share moved" in reason for reason in made.reasons)

    def test_an_unmeasured_companion_is_not_a_passing_row(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.ABSTENTION_QUALITY),
            E.observation(
                MetricId.ABSTENTION_QUALITY, numerator=9, denominator=100, baseline=0.2, interval=(0.05, 0.13)
            ),
        )
        assert made.status is MetricStatus.FAIL
        assert any("was not measured" in reason for reason in made.reasons)

    def test_a_rework_count_that_omits_one_of_its_two_halves_fails(self) -> None:
        """Section 10.6's unacceptable value for this row is a numerator with one half missing."""
        made = evaluate_metric(
            dictionary_row(MetricId.REWORK_RATE),
            E.observation(
                MetricId.REWORK_RATE,
                numerator=3,
                denominator=5,
                segments={"reruns": 3.0},
            ),
        )
        assert made.status is MetricStatus.FAIL
        assert any("revisions" in reason for reason in made.reasons)

    def test_a_companion_with_no_baseline_cannot_be_checked(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.ABSTENTION_QUALITY),
            E.observation(
                MetricId.ABSTENTION_QUALITY,
                numerator=9,
                denominator=100,
                baseline=0.2,
                interval=(0.05, 0.13),
                companions={"coarse_label_share": 0.10},
            ),
        )
        assert any("never measured for the baseline arm" in reason for reason in made.reasons)


class TestTargetsFloorsAndViolations:
    def test_a_breached_control_blocks_whatever_the_rate_says(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.UNSUPPORTED_CONCLUSION_RATE),
            E.observation(
                MetricId.UNSUPPORTED_CONCLUSION_RATE,
                numerator=1,
                denominator=100,
                violations=["a conclusion that approved an egress"],
            ),
        )
        assert made.status is MetricStatus.FAIL
        assert made.release_blocking

    def test_a_hard_floor_blocks_and_a_missed_target_does_not(self) -> None:
        blocked = evaluate_metric(
            dictionary_row(MetricId.CITATION_TRACEABILITY),
            E.observation(MetricId.CITATION_TRACEABILITY, numerator=85, denominator=100),
        )
        reported = evaluate_metric(
            dictionary_row(MetricId.SUPPORT_VALIDITY),
            E.observation(MetricId.SUPPORT_VALIDITY, numerator=80, denominator=100),
        )
        assert blocked.release_blocking and blocked.status is MetricStatus.FAIL
        assert not reported.release_blocking and reported.status is MetricStatus.FAIL

    def test_the_safety_row_blocks_when_it_was_never_measured(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.SAFETY_GATES), E.observation(MetricId.SAFETY_GATES)
        )
        assert made.status is MetricStatus.UNMEASURED
        assert made.release_blocking

    def test_an_unmeasured_ordinary_row_gates_nothing(self) -> None:
        made = evaluate_metric(dictionary_row(MetricId.CALIBRATION), E.observation(MetricId.CALIBRATION))
        assert made.status is MetricStatus.UNMEASURED
        assert not made.release_blocking

    def test_a_rate_the_collection_point_computed_is_used_as_filed(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.CALIBRATION), E.observation(MetricId.CALIBRATION, value=0.07)
        )
        assert made.value == 0.07
        assert made.status is MetricStatus.PASS


class TestTheEvaluator:
    def test_an_observation_for_another_row_is_refused(self) -> None:
        with pytest.raises(ValueError, match="the row is"):
            evaluate_metric(
                dictionary_row(MetricId.CALIBRATION),
                E.observation(MetricId.BYPASS_RATE, numerator=1, denominator=100),
            )

    def test_only_the_asked_for_rows_are_scored(self) -> None:
        observations = [E.passing_observation(one) for one in MetricId]
        made = evaluate_all(observations, only=[MetricId.CALIBRATION, MetricId.BYPASS_RATE])
        assert [one.metric_id for one in made] == [MetricId.CALIBRATION, MetricId.BYPASS_RATE]

    def test_every_filed_observation_is_scored_by_default(self) -> None:
        observations = [E.passing_observation(one) for one in MetricId]
        assert len(evaluate_all(observations)) == len(METRIC_DICTIONARY)

    def test_a_verdict_serialises_for_the_report(self) -> None:
        made = evaluate_metric(
            dictionary_row(MetricId.CITATION_TRACEABILITY),
            E.observation(MetricId.CITATION_TRACEABILITY, numerator=85, denominator=100),
        )
        row = made.as_dict()
        assert row["metric_id"] == "citation_traceability"
        assert row["status"] == "fail"
        assert row["release_blocking"] is True
        assert row["value"] == 0.85
