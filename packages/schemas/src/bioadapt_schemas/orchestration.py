"""Workflow state machine and approval records (plan V2.0 sections 9.2, 11.6, 13.1).

Two append-shaped objects back the orchestrator: a run event log, which is the only place a state
change is justified, and an approval, which is the only thing that can let a gated step run. Both
carry the identity of what was decided *over* - an approval is worthless once the plan or node it
names has changed, so it is pinned to a digest rather than to a version string alone.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any, Optional

from pydantic import Field, field_validator, model_validator

from .entities import Contract, ImmutableContract, require_hex64, sha256_of_json
from .enums import ApprovalKind, ApprovalState, RunEventKind, RunState, SubjectKind
from .identifiers import sha256_hex
from .plan import PlanNode

#: Event kinds that describe work done on a specific node rather than a change of run state.
NODE_SCOPED_EVENTS = frozenset({RunEventKind.NODE_RETRY, RunEventKind.BUSINESS_RETRY, RunEventKind.USAGE})


def run_event_id(
    run_id: str,
    kind: RunEventKind,
    *,
    node_id: Optional[str],
    attempt: int,
    from_state: RunState,
    to_state: RunState,
    loop_iteration: int = 0,
) -> str:
    """Section 9.2: a repeated callback with the same attempt resolves to the event already stored.

    A loop pass repeats the same edge (TARGETED_ANALYSIS -> TARGETED_ANALYSIS), so the iteration
    number, not the attempt, is what identifies it: pass 3 is a new event even when pass 2 and
    pass 3 both arrive as attempt 1.
    """
    seed = "|".join(
        (
            run_id,
            str(kind),
            node_id or "-",
            str(attempt),
            str(loop_iteration),
            str(from_state),
            str(to_state),
        )
    )
    return f"evt_{sha256_hex(seed.encode())[:12]}"


def new_approval_id(run_id: str, kind: ApprovalKind, subject_digest: str, node_id: Optional[str]) -> str:
    seed = "|".join((run_id, str(kind), subject_digest, node_id or "-"))
    return f"apr_{sha256_hex(seed.encode())[:12]}"


def node_subject_digest(node: PlanNode) -> str:
    """The digest one node's approval is addressed to (§9.6).

    The state machine and the workflow engine both have to name the same approval - the engine says
    which grant would let a gated node run - so the recipe lives with the contract rather than being
    written twice. It is the node's own JSON, never the plan's: editing another node must not invalidate
    this one's decision.
    """
    return sha256_hex(sha256_of_json(node.model_dump(mode="json")).encode())


def node_approval_id(run_id: str, node: PlanNode) -> str:
    return new_approval_id(run_id, ApprovalKind.NODE, node_subject_digest(node), node.node_id)


class RunEvent(ImmutableContract):
    """One line of the run's journal: every transition, every retry, every unit of spend."""

    event_id: str
    run_id: str
    kind: RunEventKind
    from_state: RunState
    to_state: RunState
    actor_id: str
    actor_kind: SubjectKind
    reason: str = Field(min_length=1)
    input_hash: str
    versions: dict[str, Any] = Field(default_factory=dict)
    attempt: int = Field(ge=1, default=1)
    node_id: Optional[str] = None
    loop_iteration: int = Field(ge=0, default=0)
    compute_minutes: float = Field(ge=0, default=0.0)
    model_cost_usd: float = Field(ge=0, default=0.0)
    at: Optional[datetime] = None

    @field_validator("input_hash")
    @classmethod
    def _hash(cls, value: str) -> str:
        return require_hex64(value, "input_hash")

    @model_validator(mode="after")
    def _kind_shape(self) -> "RunEvent":
        if self.kind is RunEventKind.STATE:
            if self.node_id is not None:
                raise ValueError("a state event is about the run, not a node")
            if self.compute_minutes or self.model_cost_usd:
                raise ValueError("spend is reported by a usage event, not by a transition")
        elif self.kind in NODE_SCOPED_EVENTS:
            if self.node_id is None:
                raise ValueError(f"a {self.kind} event must name the node it is about")
            if self.from_state is not self.to_state:
                raise ValueError(f"a {self.kind} event reports work inside {self.from_state}; it does not change state")
        if self.kind is RunEventKind.USAGE and not (self.compute_minutes or self.model_cost_usd):
            raise ValueError("a usage event must report compute minutes, model cost, or both")
        return self


class Approval(Contract):
    """Section 11.6: gating state is created and moved only by a human action."""

    approval_id: str
    run_id: str
    kind: ApprovalKind
    subject_digest: str
    requested_by: str
    #: Section 9.2: someone is accountable for deciding, and only that person (or their delegate) may.
    assigned_to: str
    requested_at: datetime
    sla_at: datetime
    state: ApprovalState = ApprovalState.PENDING
    node_id: Optional[str] = None
    decided_by: Optional[str] = None
    decided_at: Optional[datetime] = None
    decision_reason: Optional[str] = None
    delegated_from: Optional[str] = None
    delegated_at: Optional[datetime] = None

    @field_validator("subject_digest")
    @classmethod
    def _digest(cls, value: str) -> str:
        return require_hex64(value, "subject_digest")

    @model_validator(mode="after")
    def _shape(self) -> "Approval":
        if self.kind in (ApprovalKind.NODE, ApprovalKind.ADHOC_TOOL) and self.node_id is None:
            raise ValueError(f"a {self.kind} approval must name the node it gates")
        if self.kind in (ApprovalKind.PLAN, ApprovalKind.ESCALATION) and self.node_id is not None:
            raise ValueError(f"a {self.kind} approval is about the run, not one node")
        decided = (self.decided_by, self.decided_at, self.decision_reason)
        if self.state in (ApprovalState.APPROVED, ApprovalState.REJECTED) and not all(decided):
            raise ValueError("a decided approval records who decided, when, and why")
        if self.state is ApprovalState.PENDING and any(v is not None for v in decided):
            raise ValueError("a pending approval has no decision yet")
        if (self.delegated_from is None) != (self.delegated_at is None):
            raise ValueError("a delegation records both the delegator and the moment")
        if self.delegated_from is not None and self.assigned_to == self.delegated_from:
            raise ValueError("a delegated approval must name the delegate it moved to")
        if self.requested_by == self.assigned_to:
            raise ValueError("an approval may not be accountable to the subject that requested it")
        return self

    def is_granted(self) -> bool:
        return self.state is ApprovalState.APPROVED


class RunCheckpoint(ImmutableContract):
    """Section 4.1: the checkpoint format is the platform's own, and it names assets, not blobs.

    A checkpoint records which assets a node produced and the digests they had. Resuming therefore
    re-reads objects through the catalog - where scope, retention and lineage still apply - instead
    of replaying an opaque state blob. A checkpoint whose inputs, parameters or registry snapshot
    no longer match is not used: that is what keeps a resumed run honest about what it recomputed.
    """

    run_id: str
    node_id: str
    attempt: int = Field(ge=1)
    tool_name: str
    tool_version: str
    input_digest: str
    parameters_sha256: str
    registry_snapshot: str
    output_asset_ids: list[str] = Field(min_length=1)
    output_digests: dict[str, str] = Field(default_factory=dict)
    compute_minutes: float = Field(ge=0, default=0.0)
    at: Optional[datetime] = None

    @field_validator("input_digest", "parameters_sha256", "registry_snapshot")
    @classmethod
    def _digests(cls, value: str) -> str:
        return require_hex64(value, "checkpoint digest")

    @model_validator(mode="after")
    def _outputs_are_named(self) -> "RunCheckpoint":
        missing = sorted(set(self.output_asset_ids) - set(self.output_digests))
        if missing:
            raise ValueError(f"checkpointed outputs without a recorded digest: {missing}")
        for asset_id, digest in self.output_digests.items():
            require_hex64(digest, f"digest of output {asset_id}")
        return self
