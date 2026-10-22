"""Approval windows and the guards on deciding them (plan V2.0 sections 9.2, 11.6, 13.1)."""

from datetime import datetime, timedelta

import pytest

from bioadapt_orchestrator.approvals import ApprovalPolicy, add_working_days, assert_delegatable, assert_decidable
from bioadapt_orchestrator.errors import ApprovalRefused
from bioadapt_schemas.enums import ApprovalKind, ApprovalState, SubjectKind
from bioadapt_schemas.orchestration import Approval

from conftest import ANALYST, DEPUTY, NOW, PI, SERVICE

DIGEST = "ab" * 32
POLICY = ApprovalPolicy(working_days=3)


def approval(**overrides) -> Approval:
    fields = {
        "approval_id": "apr_0001",
        "run_id": "run_project001_02",
        "kind": ApprovalKind.PLAN,
        "subject_digest": DIGEST,
        "requested_by": ANALYST,
        "assigned_to": PI,
        "requested_at": NOW,
        "sla_at": POLICY.deadline(NOW),
    }
    fields.update(overrides)
    return Approval(**fields)


def test_a_window_of_three_working_days_skips_the_weekend():
    # 2026-05-01 is a Friday: Monday, Tuesday, Wednesday - not Saturday.
    assert POLICY.deadline(NOW) == datetime(2026, 5, 6, 9, 0, tzinfo=NOW.tzinfo)
    assert add_working_days(datetime(2026, 5, 8, 9, 0), 1) == datetime(2026, 5, 11, 9, 0)


def test_a_window_has_to_be_measured_in_working_days():
    with pytest.raises(ValueError):
        ApprovalPolicy(working_days=0)


def test_a_lapsed_window_is_overdue_and_stays_overdue_never_approved():
    later = NOW + timedelta(days=6)
    assert POLICY.is_overdue(approval(), later) is True
    assert POLICY.overdue_reason(approval(), later) == "open 24 h past its 3-working-day window"
    assert POLICY.is_overdue(approval(), NOW) is False
    assert POLICY.is_overdue(approval(state=ApprovalState.APPROVED, decided_by=PI, decided_at=NOW, decision_reason="ok"), later) is False


def test_a_service_account_cannot_grant_an_approval():
    with pytest.raises(ApprovalRefused, match="human decision"):
        assert_decidable(approval(), actor_id=SERVICE, actor_kind=SubjectKind.SERVICE)


def test_the_subject_that_asked_for_the_gate_may_not_open_it():
    with pytest.raises(ApprovalRefused, match="may not open it"):
        assert_decidable(approval(), actor_id=ANALYST, actor_kind=SubjectKind.HUMAN)


def test_only_the_person_accountable_for_the_approval_decides_it():
    with pytest.raises(ApprovalRefused, match=PI):
        assert_decidable(approval(), actor_id=DEPUTY, actor_kind=SubjectKind.HUMAN)
    assert_decidable(approval(), actor_id=PI, actor_kind=SubjectKind.HUMAN)


def test_a_decision_is_final_until_the_subject_changes():
    decided = approval(
        state=ApprovalState.APPROVED, decided_by=PI, decided_at=NOW, decision_reason="design is sound"
    )
    with pytest.raises(ApprovalRefused, match="already approved"):
        assert_decidable(decided, actor_id=PI, actor_kind=SubjectKind.HUMAN)


def test_an_approval_may_not_be_accountable_to_the_subject_that_requested_it():
    with pytest.raises(ValueError, match="accountable"):
        approval(requested_by=PI, assigned_to=PI)


def test_a_granted_approval_records_who_when_and_why():
    with pytest.raises(ValueError, match="who decided"):
        approval(state=ApprovalState.APPROVED, decided_by=PI)
    with pytest.raises(ValueError, match="no decision"):
        approval(decided_by=PI)


def test_delegation_needs_the_accountable_approver_and_a_different_person():
    with pytest.raises(ApprovalRefused, match="only"):
        assert_delegatable(approval(), actor_id=DEPUTY, actor_kind=SubjectKind.HUMAN, to_subject=DEPUTY)
    with pytest.raises(ApprovalRefused, match="delegating to yourself"):
        assert_delegatable(approval(), actor_id=PI, actor_kind=SubjectKind.HUMAN, to_subject=PI)
    with pytest.raises(ApprovalRefused, match="requested the gate"):
        assert_delegatable(approval(), actor_id=PI, actor_kind=SubjectKind.HUMAN, to_subject=ANALYST)
    assert_delegatable(approval(), actor_id=PI, actor_kind=SubjectKind.HUMAN, to_subject=DEPUTY)


def test_a_delegation_never_moves_after_the_decision():
    decided = approval(
        state=ApprovalState.REJECTED, decided_by=PI, decided_at=NOW, decision_reason="not this design"
    )
    with pytest.raises(ApprovalRefused, match="already rejected"):
        assert_delegatable(decided, actor_id=PI, actor_kind=SubjectKind.HUMAN, to_subject=DEPUTY)


def test_a_delegated_approval_names_the_person_it_moved_to():
    with pytest.raises(ValueError, match="delegated approval must name"):
        approval(assigned_to=PI, delegated_from=PI, delegated_at=NOW)
    moved = approval(assigned_to=DEPUTY, delegated_from=PI, delegated_at=NOW)
    assert moved.assigned_to == DEPUTY and moved.delegated_from == PI


def test_solo_mode_allows_self_deciding_and_non_assigned_deciding():
    appr = approval(requested_by=ANALYST, assigned_to=PI)
    # 严格模式下，申请人自批报 ApprovalRefused
    with pytest.raises(ApprovalRefused, match="requested the gate"):
        assert_decidable(appr, actor_id=ANALYST, actor_kind=SubjectKind.HUMAN, solo_mode=False)

    # 单人模式下，申请人自批成功通过
    assert_decidable(appr, actor_id=ANALYST, actor_kind=SubjectKind.HUMAN, solo_mode=True)
    # 单人模式下，非 assigned_to 决策也成功通过
    assert_decidable(appr, actor_id="user_solo", actor_kind=SubjectKind.HUMAN, solo_mode=True)
