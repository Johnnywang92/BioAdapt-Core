"""The state graph is the plan's §9.2 diagram (plan V2.0 sections 9.1, 9.2)."""

import pytest

from bioadapt_orchestrator.graph import (
    APPROVAL_GATED_TARGETS,
    EXCEPTION_TARGETS,
    HUMAN_ONLY_TARGETS,
    MAIN_CHAIN,
    RESUMABLE,
    TERMINAL,
    WAITING,
    describes_plan,
    is_loop_iteration,
    legal_targets,
)
from bioadapt_schemas.enums import RunState

ALL_STATES = set(RunState)


def test_every_state_the_contract_names_is_placed_in_the_graph():
    unexplained = sorted(str(s) for s in ALL_STATES - set(MAIN_CHAIN) - EXCEPTION_TARGETS)
    assert unexplained == []


def test_the_happy_path_walks_the_plan_diagram_in_order():
    for source, target in zip(MAIN_CHAIN, MAIN_CHAIN[1:], strict=False):
        assert target in legal_targets(source), f"{source} -> {target} missing"


def test_the_chain_has_no_shortcuts():
    def onward(state):
        return {s for s in legal_targets(state) if s in MAIN_CHAIN}

    assert onward(RunState.CREATED) == {RunState.PREFLIGHT_RUNNING}
    assert onward(RunState.PLAN_DRAFTED) == {RunState.PLAN_APPROVED}
    assert onward(RunState.REPORT_READY) == {RunState.ARCHIVED}
    assert RunState.ANALYSIS_RUNNING not in legal_targets(RunState.PLAN_DRAFTED)


def test_the_loop_is_optional_forward_and_bounded_on_reentry():
    assert legal_targets(RunState.EVIDENCE_BUILDING) >= {RunState.TARGETED_ANALYSIS, RunState.VALIDATION}
    assert RunState.TARGETED_ANALYSIS in legal_targets(RunState.TARGETED_ANALYSIS)
    assert is_loop_iteration(RunState.TARGETED_ANALYSIS)
    assert not is_loop_iteration(RunState.VALIDATION)


def test_an_expert_returning_a_card_reenters_the_loop_rather_than_restarting_the_run():
    assert RunState.TARGETED_ANALYSIS in legal_targets(RunState.HUMAN_REVIEW)
    assert RunState.ANALYSIS_RUNNING not in legal_targets(RunState.HUMAN_REVIEW)


def test_an_exception_branch_is_reachable_from_anything_that_is_still_working():
    for state in MAIN_CHAIN:
        if state in TERMINAL:
            continue
        assert EXCEPTION_TARGETS - {state} <= legal_targets(state), f"{state} has no way out"


def test_a_terminal_state_never_moves_again():
    for state in TERMINAL:
        assert legal_targets(state) == frozenset()
        assert legal_targets(state, resume_to=RunState.ANALYSIS_RUNNING) == frozenset()


def test_a_waiting_state_resumes_only_to_the_state_it_stopped_in():
    resumed = legal_targets(RunState.RETRYABLE_FAILURE, resume_to=RunState.ANALYSIS_RUNNING)
    assert resumed == {RunState.ANALYSIS_RUNNING, RunState.FAILED}
    assert RunState.QC_REVIEW not in legal_targets(RunState.RETRYABLE_FAILURE, resume_to=RunState.ANALYSIS_RUNNING)


@pytest.mark.parametrize("state", sorted(WAITING - RESUMABLE, key=str))
def test_a_branch_that_needs_a_decision_does_not_reopen_by_itself(state):
    targets = legal_targets(state, resume_to=RunState.ANALYSIS_RUNNING)
    assert RunState.ANALYSIS_RUNNING not in targets
    assert RunState.FAILED in targets


def test_a_budget_or_policy_block_goes_to_a_person_not_back_to_work():
    assert RunState.ESCALATED_TO_HUMAN in legal_targets(RunState.BUDGET_EXCEEDED)
    assert RunState.ESCALATED_TO_HUMAN in legal_targets(RunState.POLICY_BLOCKED)
    assert legal_targets(RunState.POLICY_BLOCKED, resume_to=RunState.TARGETED_ANALYSIS) == {
        RunState.ESCALATED_TO_HUMAN,
        RunState.FAILED,
    }


def test_a_gate_block_re_runs_the_gate():
    assert RunState.PREFLIGHT_RUNNING in legal_targets(RunState.BLOCKED)


def test_an_agent_cannot_end_a_run_or_declare_its_deliverable_ready():
    assert HUMAN_ONLY_TARGETS == frozenset({RunState.REPORT_READY, RunState.ARCHIVED, RunState.FAILED})
    assert RunState.FAILED in legal_targets(RunState.ANALYSIS_RUNNING)


def test_plan_approval_is_the_state_that_only_reflects_a_human_approval():
    assert APPROVAL_GATED_TARGETS == frozenset({RunState.PLAN_APPROVED})


def test_the_graph_describes_only_states_of_the_plan():
    assert all(describes_plan(state) for state in ALL_STATES)
