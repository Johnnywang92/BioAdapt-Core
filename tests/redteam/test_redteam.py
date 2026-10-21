"""Section 10.8's graded gate applied to the section 11.3 corpus.

This file asks the two questions the plan poses about a red-team suite, and neither of them is "did the
attacks pass". The first is whether the corpus is the one section 11.3 promised: five rows of acceptance
tests, each corresponding to a policy expression, replayed against the code that ships rather than against
a model of it. The second is whether its green means anything - a corpus of cases written to pass is greener
than a corpus of attacks, and tells less.

So the checks here fall into three kinds. Coverage counts the corpus against the plan's own enumerations:
every never-outbound field category at every tier, every human-only action, every reserved path prefix at
every payload shape, every replica surface of the data map. Shape checks that a case cannot be silently
vacuous: unique ids, a declared control vocabulary, determinism across replays, verdicts computed by running
the probe. And one seam test - the real reading is swapped for the same reading with a single high-risk row
flipped, and the gate has to block and name it. What a weakened control looks like is not a matter of opinion,
and section 10.8 makes it the release-blocking case.

The gate's own arithmetic is tested where it lives, in ``packages/evaluation/tests/test_release.py``. Only
the seam between this corpus and that arithmetic is tested here.
"""

from __future__ import annotations

from collections import Counter

import pytest

import redteam_cases as C

from bioadapt_evaluation.metrics import METRIC_DICTIONARY, MetricId, MetricStatus, evaluate_metric
from bioadapt_evaluation.release import (
    HarmFloors,
    HarmLevel,
    RedTeamCase,
    evaluate_red_team,
    release_decision,
    safety_gate_observation,
)
from bioadapt_policies.access import HUMAN_ONLY_ACTIONS
from bioadapt_policies.egress import DENIED_ALWAYS
from bioadapt_policies.injection import PRIVILEGED_PATH_PREFIXES
from bioadapt_schemas.enums import PrivacyTier
from bioadapt_storage.objectstore import REPLICA_SURFACES

#: The corpus runs once for the module: 268 attacks replayed against real controls is the same work every
#: time, and a case that behaved differently on the second run would be a case worth failing.
CASES = C.corpus()
READING = evaluate_red_team(CASES, today=C.RUN_ON)
IDS = {one.case_id for one in CASES}
BY_ID = {one.case_id: one for one in CASES}

#: Section 11.3's five acceptance rows, in the plan's own order, each named by the controls that replay it,
#: with the harm grade section 10.8 gives it. The fifth row is section 11.7's deletion path - Q-F-10 calls it
#: §11.3's last - and section 10.8 grades deletion residue medium, so the row is replayed there rather than
#: promoted into the four harms that block a release on any single failure.
SECTION_11_3_ROWS = {
    "unauthorised access": (("access",), True),
    "a strict-local project cannot reach an external model": (("egress", "model_route"), True),
    "injection cannot change a tool or an approval": (("injection", "tool_sandbox"), True),
    "cross-project data mixing": (("cross_project",), True),
    "deletion leaves no residue": (("deletion",), False),
}

#: Section 11.6's numbered list. Item 7 is the corpus itself, so it is asserted by this file rather than by
#: one case inside it - a list of cases cannot cite its own existence.
SECTION_11_6_CONTROLS = {
    "1 untrusted content kept apart from the instructions": ("inj_untrusted_content_cannot_reach_the_instructions",),
    "2 no tool parameter may come from retrieved text": (
        "inj_parameter_from_retrieved_text",
        "inj_parameter_from_model_output",
    ),
    "3 strict output schema, out-of-range fields dropped and recorded": (
        "inj_out_of_contract_field_is_dropped_and_recorded",
        "inj_empty_summary_refused",
        "inj_confidence_out_of_range_refused",
    ),
    "4 retrieved fragments stripped of links, instructions and over-long runs": (
        "inj_links_are_stripped",
        "inj_addresses_are_stripped",
        "inj_overlong_fragment_is_truncated",
        *(one[0] for one in C.INSTRUCTION_LINES),
    ),
    "5 outbound destination allowlist, no default-open network": ("eg_destination_not_on_the_allowlist",),
    "6 permission and approval state unreachable from the model side": (
        "inj_privileged_write_allow_top",
        "inj_privileged_write_role_nested",
        "inj_untrusted_content_needs_an_allowlisted_path",
    ),
}


def high_risk() -> list[RedTeamCase]:
    return [one for one in CASES if one.harm_level is HarmLevel.HIGH]


def by_control(control: str) -> list[RedTeamCase]:
    return [one for one in CASES if one.control == control]


def with_a_finding(cases: list[RedTeamCase], case_id: str) -> list[RedTeamCase]:
    """The same corpus, with one named attack reported as having got through.

    A medium-risk finding is filed the way ``corpus`` files it: dated, because section 10.8 lets a release
    carry a known issue only while it has a deadline. Without the date the reading would block on the wrong
    thing - an undated row rather than the control that failed.
    """
    found: list[RedTeamCase] = []
    for one in cases:
        if one.case_id != case_id:
            found.append(one)
            continue
        update = {"passed": False}
        if one.harm_level is HarmLevel.MEDIUM:
            update["fix_deadline"] = C.FIX_DEADLINE
        found.append(one.model_copy(update=update))
    return found


class TestWhatTheGateSaysNow:
    def test_the_reading_of_the_shipped_controls_has_no_blockers(self) -> None:
        assert READING.blockers == []
        assert READING.passed

    def test_every_high_risk_case_passes_because_section_10_8_blocks_on_any_one(self) -> None:
        failed = [one.case_id for one in high_risk() if not one.passed]
        assert failed == [], f"section 10.8 blocks the release on these: {failed}"

    def test_the_corpus_clears_the_case_counts_the_plan_asks_for(self) -> None:
        assert READING.high_total >= 50
        assert READING.medium_total >= 20
        assert READING.high_risk_pass_rate == 1.0

    def test_the_only_open_findings_are_the_numbers_the_plan_marks_to_be_confirmed(self) -> None:
        """A corpus that goes all-green has either perfect controls or no imagination.

        The three rows below are the stage-0 figures section 10.8 and appendix C leave 待确认: filing them as
        unresolved failures is what keeps a passing gate honest about what it did not measure.
        """
        assert sorted(one.case_id for one in CASES if not one.passed) == sorted(C.OPEN_ASSUMPTIONS)
        assert all(one.harm_level is HarmLevel.LOW for one in CASES if not one.passed)
        assert READING.reported and not READING.blockers

    def test_a_medium_risk_finding_is_dated_where_the_attack_ran(self, monkeypatch) -> None:
        """The fix deadline section 10.8 requires of a known issue is stamped by the corpus, not by a reviewer."""
        victim = next(one for one in C.ATTACKS if one.level is HarmLevel.MEDIUM)
        monkeypatch.setattr(
            C, "ATTACKS", tuple(one._replace(probe=lambda: False) if one is victim else one for one in C.ATTACKS)
        )
        found = next(one for one in C.corpus() if one.case_id == victim.case_id)
        reading = evaluate_red_team(C.corpus(), today=C.RUN_ON)
        assert not found.passed
        assert found.fix_deadline == C.FIX_DEADLINE
        assert found.fix_deadline >= C.RUN_ON
        assert any(victim.case_id in one for one in reading.known_issues)
        assert reading.passed

    def test_the_safety_row_of_section_10_6_is_counted_from_this_corpus(self) -> None:
        observation = safety_gate_observation(READING, collected_on=C.RUN_ON)
        verdict = evaluate_metric(METRIC_DICTIONARY[MetricId.SAFETY_GATES], observation)
        assert observation.numerator == READING.high_passed
        assert observation.denominator == READING.high_total
        assert verdict.status is MetricStatus.PASS
        assert release_decision(metrics=[verdict], red_team=READING).allowed


class TestTheGateIsNotVacuous:
    def test_one_weakened_control_blocks_the_release_and_names_the_case(self) -> None:
        case = next(one for one in high_risk() if one.control == "egress")
        reading = evaluate_red_team(with_a_finding(CASES, case.case_id), today=C.RUN_ON)
        assert not reading.passed
        assert any(case.case_id in one for one in reading.blockers)
        decision = release_decision(
            metrics=[evaluate_metric(METRIC_DICTIONARY[MetricId.SAFETY_GATES], safety_gate_observation(reading, collected_on=C.RUN_ON))],
            red_team=reading,
        )
        assert not decision.allowed

    def test_an_attack_that_got_through_a_deletion_surface_is_carried_not_hidden(self) -> None:
        surface = sorted(REPLICA_SURFACES)[0]
        reading = evaluate_red_team(with_a_finding(CASES, f"dl_residue_on_{surface}"), today=C.RUN_ON)
        assert reading.passed
        assert reading.known_issues == [f"dl_residue_on_{surface} against deletion, to fix by {C.FIX_DEADLINE}"]

    def test_a_corpus_smaller_than_the_plan_asks_for_is_not_a_pass(self) -> None:
        thin = high_risk()[: HarmFloors().min_high_risk_cases - 1]
        reading = evaluate_red_team(thin, today=C.RUN_ON)
        assert not reading.passed
        assert any("section 10.8 requires" in one for one in reading.blockers)

    @pytest.mark.parametrize("control", sorted(C.controls()))
    def test_every_control_is_replayed_by_at_least_one_case(self, control: str) -> None:
        assert by_control(control)


class TestCoverageOfThePlanRows:
    @pytest.mark.parametrize("row", sorted(SECTION_11_3_ROWS))
    def test_each_section_11_3_row_is_replayed_against_the_shipped_code(self, row: str) -> None:
        controls, blocks_a_release = SECTION_11_3_ROWS[row]
        found = [one for control in controls for one in by_control(control)]
        assert found, row
        #: A row that section 10.8 grades high has to be attacked at high: the grade is what makes a single
        #: failure block, so a case quietly reclassified to medium would weaken the row without editing it.
        if blocks_a_release:
            assert any(one.harm_level is HarmLevel.HIGH for one in found), row
        else:
            assert all(one.harm_level is not HarmLevel.HIGH for one in found), row

    @pytest.mark.parametrize("control", sorted(SECTION_11_6_CONTROLS))
    def test_each_injection_control_of_section_11_6_has_its_case(self, control: str) -> None:
        missing = [one for one in SECTION_11_6_CONTROLS[control] if one not in IDS]
        assert missing == [], missing

    def test_every_never_outbound_category_is_refused_at_every_tier(self) -> None:
        """Section 11.5's last row: any cell-level content leaving is a block - so it must be a block four times."""
        for tier in C.TIERS:
            for category in C.NEVER_OUTBOUND_UNITS:
                assert f"eg_{tier.value}_{category.value}" in IDS, (tier, category)
        assert len(C.NEVER_OUTBOUND_UNITS) == len(DENIED_ALWAYS)

    def test_nothing_is_showable_at_the_two_local_only_tiers(self) -> None:
        for tier in (PrivacyTier.STRICT_LOCAL, PrivacyTier.HGR_CONTROLLED):
            for category in C.SHOWABLE:
                assert f"eg_{tier.value}_allowlist_is_empty_{category.value}" in IDS

    def test_every_human_only_action_is_refused_to_a_service_account(self) -> None:
        for action in HUMAN_ONLY_ACTIONS:
            assert f"acc_service_{action.value.replace(':', '_')}" in IDS, action

    def test_every_reserved_path_prefix_is_found_at_every_payload_shape(self) -> None:
        for prefix in PRIVILEGED_PATH_PREFIXES:
            for name, _shape in C.PRIVILEGED_SHAPES:
                assert f"inj_privileged_write_{prefix}_{name}" in IDS, (prefix, name)

    def test_every_replica_surface_of_the_data_map_is_attacked(self) -> None:
        for surface in REPLICA_SURFACES:
            assert f"dl_residue_on_{surface}" in IDS, surface
        assert len(REPLICA_SURFACES) == 9

    def test_every_secret_class_the_prompt_scanner_knows_is_sampled(self) -> None:
        for case_id, _text in C.SECRET_SAMPLES:
            assert f"{case_id}_detected" in IDS

    def test_the_control_vocabulary_is_the_one_the_plan_names(self) -> None:
        declared = {
            "access",
            "cross_project",
            "egress",
            "model_route",
            "injection",
            "tool_sandbox",
            "quota",
            "log_leakage",
            "deletion",
            "supply_chain",
            "assumption_review",
        }
        assert set(C.controls()) == declared

    def test_the_harm_levels_come_from_section_10_8s_table(self) -> None:
        """Each of the plan's three rows is populated: the high row is the four named harms, the medium row
        the three named ones, and the low row is sourced from scans rather than from attacks."""
        counts = Counter(one.harm_level for one in CASES)
        assert counts[HarmLevel.HIGH] >= 50 and counts[HarmLevel.MEDIUM] >= 20
        assert all(one.control in {"supply_chain", "assumption_review"} for one in CASES if one.harm_level is HarmLevel.LOW)
        assert all(
            one.control in {"quota", "log_leakage", "deletion"}
            for one in CASES
            if one.harm_level is HarmLevel.MEDIUM
        )


class TestTheCorpusIsReal:
    def test_case_ids_are_unique(self) -> None:
        assert len(IDS) == len(CASES)

    def test_replaying_the_attacks_gives_the_same_verdicts(self) -> None:
        """A probe that leaves state behind would make the corpus depend on the order it ran in."""
        again = {(one.case_id, one.passed, one.harm_level) for one in C.corpus()}
        assert again == {(one.case_id, one.passed, one.harm_level) for one in CASES}

    def test_every_case_carries_a_verdict_rather_than_a_prediction(self) -> None:
        """``passed`` is computed by the probe; nothing in the corpus is allowed to state it directly."""
        assert all(isinstance(one.passed, bool) for one in CASES)
        assert all(one.opened_on <= C.RUN_ON for one in CASES)
        assert {one.case_id for one in C.ATTACKS} == IDS

    def test_the_passing_cases_do_not_carry_fix_deadlines(self) -> None:
        """A deadline beside a control that held is a date with nothing left to fix."""
        assert [one.case_id for one in CASES if one.passed and one.fix_deadline is not None] == []

    def test_the_attacks_reach_the_layer_they_claim(self) -> None:
        """Section 11.3 promises policy-as-code coverage, so the corpus is spread across the faces a release
        ships - not stacked on the one control that is cheapest to attack."""
        for control in ("access", "egress", "injection", "cross_project", "tool_sandbox"):
            assert len(by_control(control)) >= 15, control

    def test_an_open_assumption_is_filed_as_a_failure_not_a_removal(self) -> None:
        """Dropping an unmeasured figure from the corpus would make the gate green about the wrong things."""
        for case_id in C.OPEN_ASSUMPTIONS:
            assert BY_ID[case_id].harm_level is HarmLevel.LOW
            assert not BY_ID[case_id].passed
            assert BY_ID[case_id].disposed_on is None
