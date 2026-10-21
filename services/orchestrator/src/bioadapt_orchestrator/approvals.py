"""Approval windows and the rules for deciding an approval (plan V2.0 sections 9.2, 11.6, 13.1).

Pure functions over :class:`Approval` so the same guards hold whether a decision arrives over HTTP
or from the workflow engine. The window length is supplied by the deployment: appendix C1 of the
plan forbids shipping the stage-0 number as a code constant.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from bioadapt_schemas.enums import ApprovalState, SubjectKind
from bioadapt_schemas.orchestration import Approval

from .errors import ApprovalRefused

#: Monday..Friday. Public-holiday calendars are a deployment input the plan has not fixed yet.
WORKDAYS = frozenset(range(5))


def add_working_days(moment: datetime, working_days: int) -> datetime:
    if working_days < 1:
        raise ValueError("an approval window must be at least one working day")
    deadline = moment
    remaining = working_days
    while remaining:
        deadline += timedelta(days=1)
        if deadline.weekday() in WORKDAYS:
            remaining -= 1
    return deadline


@dataclass(frozen=True)
class ApprovalPolicy:
    working_days: int

    def __post_init__(self) -> None:
        if self.working_days < 1:
            raise ValueError("approval_window must be at least one working day")

    def deadline(self, requested_at: datetime) -> datetime:
        return add_working_days(requested_at, self.working_days)

    def is_overdue(self, approval: Approval, now: datetime) -> bool:
        """Section 9.2: a lapsed window is an escalation trigger, never an automatic approval."""
        return approval.state is ApprovalState.PENDING and now > approval.sla_at

    def overdue_reason(self, approval: Approval, now: datetime) -> str:
        overdue = now - approval.sla_at
        return f"open {int(overdue.total_seconds() // 3600)} h past its {self.working_days}-working-day window"


def assert_decidable(approval: Approval, *, actor_id: str, actor_kind: SubjectKind) -> None:
    if approval.state is not ApprovalState.PENDING:
        raise ApprovalRefused(f"approval {approval.approval_id} is already {approval.state}")
    if actor_kind is not SubjectKind.HUMAN:
        raise ApprovalRefused("an approval is a human decision; a service account cannot record one")
    if actor_id == approval.requested_by:
        raise ApprovalRefused(
            "the subject that requested the gate may not open it (section 9.2: approval is never the "
            "submitter's own act)"
        )
    if actor_id != approval.assigned_to:
        raise ApprovalRefused(
            f"approval {approval.approval_id} is accountable to {approval.assigned_to}; "
            "delegate it to move the accountability"
        )


def assert_delegatable(
    approval: Approval, *, actor_id: str, actor_kind: SubjectKind, to_subject: str
) -> None:
    if approval.state is not ApprovalState.PENDING:
        raise ApprovalRefused(f"approval {approval.approval_id} is already {approval.state}")
    if actor_kind is not SubjectKind.HUMAN:
        raise ApprovalRefused("only a person holding the approval can delegate it")
    if actor_id != approval.assigned_to:
        raise ApprovalRefused(f"only {approval.assigned_to} may delegate this approval")
    if to_subject == approval.assigned_to:
        raise ApprovalRefused("delegating to yourself is not a delegation")
    if to_subject == approval.requested_by:
        raise ApprovalRefused(
            "this approval cannot be delegated back to the subject that requested the gate"
        )
