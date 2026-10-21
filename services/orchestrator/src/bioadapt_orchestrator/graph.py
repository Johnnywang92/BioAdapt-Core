"""The run state machine (plan V2.0 section 9.2).

The graph is the plan's diagram, written down once: a forward chain, an explicit set of exception
branches, and the narrow ways back out of each branch. Nothing else may be persisted as a run state,
which is what makes "the run is in a state the plan does not describe" impossible rather than rare.
"""

from __future__ import annotations

from bioadapt_schemas.enums import RunState

#: The chain, in order. Section 9.2 draws EVIDENCE_BUILDING -> TARGETED_ANALYSIS -> VALIDATION with
#: the loop optional, so leaving the loop forward to VALIDATION is legal and re-entry is the edge
#: that consumes budget.
MAIN_CHAIN: tuple[RunState, ...] = (
    RunState.CREATED,
    RunState.PREFLIGHT_RUNNING,
    RunState.PREFLIGHT_PASSED,
    RunState.PLAN_DRAFTED,
    RunState.PLAN_APPROVED,
    RunState.ANALYSIS_RUNNING,
    RunState.QC_REVIEW,
    RunState.CANDIDATE_DISCOVERY,
    RunState.EVIDENCE_BUILDING,
    RunState.TARGETED_ANALYSIS,
    RunState.VALIDATION,
    RunState.HUMAN_REVIEW,
    RunState.REPORT_READY,
    RunState.ARCHIVED,
)

FORWARD: dict[RunState, frozenset[RunState]] = {
    RunState.CREATED: frozenset({RunState.PREFLIGHT_RUNNING}),
    RunState.PREFLIGHT_RUNNING: frozenset({RunState.PREFLIGHT_PASSED}),
    RunState.PREFLIGHT_PASSED: frozenset({RunState.PLAN_DRAFTED}),
    RunState.PLAN_DRAFTED: frozenset({RunState.PLAN_APPROVED}),
    RunState.PLAN_APPROVED: frozenset({RunState.ANALYSIS_RUNNING}),
    RunState.ANALYSIS_RUNNING: frozenset({RunState.QC_REVIEW}),
    RunState.QC_REVIEW: frozenset({RunState.CANDIDATE_DISCOVERY}),
    RunState.CANDIDATE_DISCOVERY: frozenset({RunState.EVIDENCE_BUILDING}),
    RunState.EVIDENCE_BUILDING: frozenset({RunState.TARGETED_ANALYSIS, RunState.VALIDATION}),
    #: The bounded loop: staying in the state is one more pass, and it is charged to the budget.
    RunState.TARGETED_ANALYSIS: frozenset({RunState.TARGETED_ANALYSIS, RunState.VALIDATION}),
    RunState.VALIDATION: frozenset({RunState.HUMAN_REVIEW}),
    #: An expert returning a card for more analysis re-enters the loop, it does not restart the run.
    RunState.HUMAN_REVIEW: frozenset({RunState.REPORT_READY, RunState.TARGETED_ANALYSIS}),
    RunState.REPORT_READY: frozenset({RunState.ARCHIVED}),
    RunState.ARCHIVED: frozenset(),
}

#: Section 9.2: the run is finished; no further transition of any kind.
TERMINAL = frozenset({RunState.ARCHIVED, RunState.FAILED})

#: Branches that can be entered from anywhere on the forward chain.
EXCEPTION_TARGETS = frozenset(
    {
        RunState.BLOCKED,
        RunState.RETRYABLE_FAILURE,
        RunState.NEEDS_INPUT,
        RunState.POLICY_BLOCKED,
        RunState.RESOURCE_EXCEEDED,
        RunState.BUDGET_EXCEEDED,
        RunState.ESCALATED_TO_HUMAN,
        RunState.FAILED,
    }
)

#: States that hold a run until something happens; they are not terminal and not progress.
WAITING = EXCEPTION_TARGETS - {RunState.FAILED}

#: From a branch, only these lead onward. Giving up is always available; going back is not.
#: An escalation is resumable because the plan says the loop stops *for a person*, and that person
#: has to be able to send it back; the approval check that bounds that is in the orchestrator.
RESUMABLE = frozenset(
    {
        RunState.RETRYABLE_FAILURE,
        RunState.NEEDS_INPUT,
        RunState.RESOURCE_EXCEEDED,
        RunState.ESCALATED_TO_HUMAN,
    }
)

#: A policy or budget branch never re-opens on its own: it goes to a human, who acts through an
#: approval (a new plan version), or it ends. A gate block re-runs the gate.
BRANCH_ONWARD: dict[RunState, frozenset[RunState]] = {
    RunState.BLOCKED: frozenset({RunState.PREFLIGHT_RUNNING}),
    RunState.POLICY_BLOCKED: frozenset({RunState.ESCALATED_TO_HUMAN}),
    RunState.BUDGET_EXCEEDED: frozenset({RunState.ESCALATED_TO_HUMAN}),
}

#: Section 9.1: an agent output is a draft; the terminal states and the deliverable declaration are
#: human acts. A service account that could set FAILED could also end a run it disagreed with.
HUMAN_ONLY_TARGETS = frozenset({RunState.REPORT_READY, RunState.ARCHIVED, RunState.FAILED})

#: Section 11.6: plan approval is a human decision reflected in the machine, never invented by it.
APPROVAL_GATED_TARGETS = frozenset({RunState.PLAN_APPROVED})

#: Section 9.2: one pass of the bounded loop is one entry into TARGETED_ANALYSIS.
LOOP_ENTRY = RunState.TARGETED_ANALYSIS


def is_main_chain(state: RunState) -> bool:
    return state in MAIN_CHAIN


def legal_targets(current: RunState, *, resume_to: RunState | None = None) -> frozenset[RunState]:
    """Everything the machine may move to from ``current``; ``resume_to`` is the state a branch was
    entered from, and only some branches allow going back there."""
    if current in TERMINAL:
        return frozenset()
    if current in WAITING:
        targets = {RunState.FAILED} | BRANCH_ONWARD.get(current, frozenset())
        if current in RESUMABLE and resume_to is not None and is_main_chain(resume_to):
            targets.add(resume_to)
        return frozenset(targets)
    return FORWARD.get(current, frozenset()) | (EXCEPTION_TARGETS - {current})


def is_loop_iteration(target: RunState) -> bool:
    return target is LOOP_ENTRY


def describes_plan(state: RunState) -> bool:
    """States the plan's §9.2 diagram names; used to keep the graph and the contract in step."""
    return state in set(MAIN_CHAIN) | EXCEPTION_TARGETS
