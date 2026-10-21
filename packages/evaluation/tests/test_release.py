"""The release gate: section 10.3's rows, section 10.8's harm levels, and what may gate at all (10.3-10.8).

Three tables decide whether a build ships, and they disagree on purpose. Section 10.3 fires on a candidate and
says what happens to it; section 10.8 counts red-team cases by harm level and lets only the high-risk row block
on a single failure; section 10.4 refuses to let a comparison gate a release before a baseline exists. This
module recomputes none of them - it looks each signal up in its own table, turns a red-team reading into the
safety row's measurement, and then decides which numbers are allowed to stop a release. The consequence is that
a block always has a name attached: a case id, a refused card, an interval that covers its baseline.
"""

from __future__ import annotations

from datetime import date

import evaluation_factories as E
from bioadapt_evaluation.metrics import (
    METRIC_DICTIONARY,
    MetricId,
    MetricStatus,
    dictionary_row,
    evaluate_all,
    evaluate_metric,
)
from bioadapt_evaluation.release import (
    GATE_RULES,
    GateTrigger,
    HarmFloors,
    HarmLevel,
    RedTeamCase,
    apply_gate_rules,
    evaluate_red_team,
    release_decision,
    safety_gate_observation,
)
from bioadapt_evaluation.truth import TruthFloors

ALL_PASSING = evaluate_all([E.passing_observation(one) for one in MetricId])


def clean_read(**overrides) -> object:
    fields = {"metrics": ALL_PASSING}
    fields.update(overrides)
    return release_decision(**fields)


class TestTheGateTable:
    def test_section_10_3_adds_five_rows_and_they_are_all_here(self) -> None:
        assert set(GATE_RULES) == set(GateTrigger)
        assert len(GATE_RULES) == 5

    def test_the_auto_recovery_column_is_kept_as_the_plan_wrote_it(self) -> None:
        auto = {
            GateTrigger.BENCHMARK_MARKED_CITATION,
            GateTrigger.EXPERT_QUEUE_OVERFLOW,
        }
        for trigger, rule in GATE_RULES.items():
            assert rule.auto_recoverable is (trigger in auto), trigger

    def test_a_fired_row_gets_the_table_s_action_not_the_call_site_s(self) -> None:
        made = apply_gate_rules([E.signal(GateTrigger.ADHOC_ONLY_SUPPORT)])
        assert len(made) == 1
        assert made[0].action == GATE_RULES[GateTrigger.ADHOC_ONLY_SUPPORT].action
        assert made[0].subject_id == "cand_001_run_0001"

    def test_a_model_written_number_refuses_the_card_and_waits_for_a_person(self) -> None:
        made = apply_gate_rules([E.signal(GateTrigger.MODEL_SOURCED_NUMERIC_FIELD, "card_0001")])
        assert made[0].blocks_release
        assert made[0].requires_human

    def test_a_masked_benchmark_source_is_the_run_s_own_business(self) -> None:
        made = apply_gate_rules([E.signal(GateTrigger.BENCHMARK_MARKED_CITATION, "ev_marker_01")])
        assert not made[0].requires_human
        assert not made[0].blocks_release

    def test_a_discovery_only_candidate_is_capped_rather_than_blocked(self) -> None:
        made = apply_gate_rules([E.signal(GateTrigger.SUPPORT_FROM_DISCOVERY_ONLY)])
        assert not made[0].blocks_release
        assert made[0].requires_human
        assert "provisional" in made[0].action


class TestTheHarmTable:
    def test_a_corpus_at_the_floors_with_nothing_failing_blocks_nothing(self) -> None:
        reading = evaluate_red_team(E.corpus(), today=E.TODAY)
        assert reading.passed
        assert reading.high_risk_pass_rate == 1.0
        assert reading.blockers == []

    def test_the_plan_s_floors_are_fifty_high_and_twenty_medium(self) -> None:
        assert HarmFloors().min_high_risk_cases == 50
        assert HarmFloors().min_medium_risk_cases == 20
        assert HarmFloors().low_risk_sla_days is None

    def test_one_high_risk_failure_blocks_and_names_the_case(self) -> None:
        reading = evaluate_red_team(
            E.corpus(high_failures=("high_007",)), today=E.TODAY
        )
        assert not reading.passed
        assert any("high_007" in reason for reason in reading.blockers)
        assert reading.failed_cases == ["high_007"]

    def test_passing_forty_nine_of_forty_nine_is_not_the_fifty_the_plan_asked_for(self) -> None:
        reading = evaluate_red_team(E.corpus(high=49), today=E.TODAY)
        assert not reading.passed
        assert any("49 high-risk cases" in reason for reason in reading.blockers)

    def test_a_thin_medium_corpus_blocks_too(self) -> None:
        reading = evaluate_red_team(E.corpus(medium=19), today=E.TODAY)
        assert any("19 medium-risk cases" in reason for reason in reading.blockers)

    def test_a_medium_failure_with_a_live_deadline_is_a_known_issue(self) -> None:
        reading = evaluate_red_team(
            E.corpus(medium_failures={"med_003": date(2026, 6, 30)}), today=E.TODAY
        )
        assert reading.passed
        assert any("med_003" in reason for reason in reading.known_issues)
        assert reading.blockers == []

    def test_a_medium_failure_without_a_deadline_is_not_a_known_issue(self) -> None:
        reading = evaluate_red_team(E.corpus(medium_failures={"med_003": None}), today=E.TODAY)
        assert any("no fix deadline" in reason for reason in reading.blockers)

    def test_an_expired_deadline_stops_tolerating_the_failure(self) -> None:
        reading = evaluate_red_team(
            E.corpus(medium_failures={"med_003": date(2026, 4, 30)}), today=E.TODAY
        )
        assert any("fix deadline" in reason for reason in reading.blockers)

    def test_a_low_risk_failure_is_reported_without_blocking(self) -> None:
        cases = [
            *E.corpus(high=50, medium=20, low=0),
            RedTeamCase(
                case_id="low_001",
                harm_level=HarmLevel.LOW,
                control="dependency_scan",
                passed=False,
                opened_on=E.TODAY,
            ),
        ]
        reading = evaluate_red_team(cases, today=E.TODAY)
        assert reading.passed
        assert any("low_001" in reason for reason in reading.reported)

    def test_an_undisposed_low_risk_finding_breaches_its_own_sla(self) -> None:
        cases = [
            *E.corpus(high=50, medium=20, low=0),
            RedTeamCase(
                case_id="low_001",
                harm_level=HarmLevel.LOW,
                control="dependency_scan",
                passed=False,
                opened_on=date(2026, 1, 1),
            ),
        ]
        reading = evaluate_red_team(cases, floors=HarmFloors(low_risk_sla_days=30), today=E.TODAY)
        assert any("disposal SLA" in reason for reason in reading.blockers)

    def test_a_disposed_low_risk_finding_is_not_overdue(self) -> None:
        cases = [
            *E.corpus(high=50, medium=20, low=0),
            RedTeamCase(
                case_id="low_001",
                harm_level=HarmLevel.LOW,
                control="dependency_scan",
                passed=False,
                opened_on=date(2026, 1, 1),
                disposed_on=date(2026, 1, 20),
            ),
        ]
        reading = evaluate_red_team(cases, floors=HarmFloors(low_risk_sla_days=30), today=E.TODAY)
        assert reading.passed
        assert reading.reported == ["low_001 against dependency_scan, disposed on 2026-01-20"]


class TestTheSafetyRowComesFromTheCorpus:
    def test_the_safety_measurement_is_counted_from_the_cases_not_typed_beside_them(self) -> None:
        reading = evaluate_red_team(E.corpus(high_failures=("high_002",)), today=E.TODAY)
        made = safety_gate_observation(reading, collected_on=E.TODAY)
        assert made.numerator == 49
        assert made.denominator == 50
        assert made.violations == reading.blockers
        verdict = evaluate_metric(dictionary_row(MetricId.SAFETY_GATES), made)
        assert verdict.status is MetricStatus.FAIL
        assert verdict.release_blocking

    def test_a_clean_corpus_produces_a_passing_safety_row(self) -> None:
        reading = evaluate_red_team(E.corpus(), today=E.TODAY)
        verdict = evaluate_metric(
            dictionary_row(MetricId.SAFETY_GATES), safety_gate_observation(reading, collected_on=E.TODAY)
        )
        assert verdict.status is MetricStatus.PASS
        assert verdict.value == 1.0

    def test_an_empty_corpus_is_unmeasured_and_still_blocks(self) -> None:
        reading = evaluate_red_team([], today=E.TODAY, floors=HarmFloors(min_high_risk_cases=0, min_medium_risk_cases=0))
        made = safety_gate_observation(reading, collected_on=E.TODAY)
        verdict = evaluate_metric(dictionary_row(MetricId.SAFETY_GATES), made)
        assert verdict.status is MetricStatus.UNMEASURED
        assert verdict.release_blocking


class TestWhatMayGate:
    def test_a_release_with_no_safety_verdict_is_not_a_passing_one(self) -> None:
        made = clean_read(
            metrics=[
                one
                for one in ALL_PASSING
                if one.metric_id is not MetricId.SAFETY_GATES
            ]
        )
        assert not made.allowed
        assert any("safety row of section 10.6 was not evaluated" in reason for reason in made.blockers)

    def test_a_clean_combination_is_allowed_and_addresses_itself(self) -> None:
        made = clean_read(red_team=evaluate_red_team(E.corpus(), today=E.TODAY))
        assert made.allowed
        assert made.blockers == []
        assert made.decision_id() == clean_read(
            red_team=evaluate_red_team(E.corpus(), today=E.TODAY)
        ).decision_id()

    def test_a_trend_only_comparison_is_reported_rather_than_used_as_a_gate(self) -> None:
        trend = evaluate_metric(
            dictionary_row(MetricId.RARE_CLASS_RECALL),
            E.observation(MetricId.RARE_CLASS_RECALL, numerator=45, denominator=60),
        )
        made = clean_read(metrics=[*ALL_PASSING, trend])
        assert made.allowed
        assert any("rare_class_recall" in reason for reason in made.trends)

    def test_below_the_truth_floors_a_failed_metric_is_a_trend_not_a_block(self) -> None:
        failed = evaluate_metric(
            dictionary_row(MetricId.CITATION_TRACEABILITY),
            E.observation(MetricId.CITATION_TRACEABILITY, numerator=93, denominator=100),
        )
        made = clean_read(
            metrics=[*ALL_PASSING, failed],
            truth=E.truth_set(report_only=True).reading(),
        )
        assert made.allowed
        assert any("below section 10.5" in reason for reason in made.trends)
        assert any("truth set: " in reason for reason in made.trends)

    def test_a_breached_control_still_blocks_below_the_truth_floors(self) -> None:
        blocked = evaluate_metric(
            dictionary_row(MetricId.SAFETY_GATES),
            E.observation(
                MetricId.SAFETY_GATES,
                numerator=49,
                denominator=50,
                violations=["a strict-local card reached a cloud endpoint"],
            ),
        )
        others = [one for one in ALL_PASSING if one.metric_id is not MetricId.SAFETY_GATES]
        made = clean_read(
            metrics=[*others, blocked],
            truth=E.truth_set(floors=TruthFloors(min_counterexample_classes=99)).reading(),
        )
        assert not made.allowed
        assert any("safety_gates" in reason for reason in made.blockers)

    def test_a_row_that_waits_for_a_person_is_listed_even_when_it_does_not_block(self) -> None:
        gates = apply_gate_rules(
            [
                E.signal(GateTrigger.SUPPORT_FROM_DISCOVERY_ONLY),
                E.signal(GateTrigger.ADHOC_ONLY_SUPPORT, "cand_002_run_0001"),
            ]
        )
        made = clean_read(gates=gates)
        assert not made.allowed
        assert any("cand_002_run_0001" in reason for reason in made.blockers)
        assert len(made.human_actions) == 2
        assert any("clears when" in reason for reason in made.human_actions)

    def test_a_medium_risk_known_issue_travels_with_the_release(self) -> None:
        made = clean_read(
            red_team=evaluate_red_team(
                E.corpus(medium_failures={"med_003": date(2026, 6, 30)}), today=E.TODAY
            )
        )
        assert made.allowed
        assert any("known issue: med_003" in reason for reason in made.noted)

    def test_the_decision_carries_the_rows_it_acted_on(self) -> None:
        made = clean_read(red_team=evaluate_red_team(E.corpus(), today=E.TODAY))
        assert len(made.metrics) == len(METRIC_DICTIONARY)
        assert all("metric_id" in one for one in made.metrics)
