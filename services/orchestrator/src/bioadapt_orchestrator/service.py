"""The orchestrator: state machine, approvals, loop budget and retries (plan V2.0 sections 9.1-9.2).

One object owns the run journal. Everything the workflow engine, an analyst or an agent wants to do
to a run goes through it, so the plan's rules - a reason for every transition, a human for every
approval, a ceiling for every loop - are enforced in one place instead of at each call site.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Optional, Protocol

from bioadapt_policies.access import Actor
from bioadapt_schemas.entities import RunManifest, sha256_of_json
from bioadapt_schemas.enums import ApprovalKind, ApprovalState, RunEventKind, RunState, SubjectKind
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_schemas.orchestration import (
    Approval,
    RunEvent,
    new_approval_id,
    node_subject_digest,
    run_event_id,
)
from bioadapt_schemas.plan import AnalysisPlan, PlanNode
from bioadapt_storage.catalog import Catalog

from .approvals import ApprovalPolicy, assert_delegatable, assert_decidable
from .budget import LoopCeiling, LoopUsage, invalid_resumption_budget
from .errors import ActorNotAllowed, ApprovalRequired, IllegalTransition
from .graph import (
    APPROVAL_GATED_TARGETS,
    HUMAN_ONLY_TARGETS,
    TERMINAL,
    is_main_chain,
    is_loop_iteration,
    legal_targets,
)
from .retry import assert_attempt_within_policy, assert_backoff_elapsed


@dataclass(frozen=True)
class ActorRef:
    """Who is acting, and whether a person is behind it (section 11.2 distinguishes the two)."""

    subject_id: str
    kind: SubjectKind
    solo_mode: bool = False

    @classmethod
    def of(cls, actor: Actor) -> "ActorRef":
        is_solo = bool(getattr(actor, "solo_mode", False))
        return cls(
            subject_id=actor.subject_id,
            kind=SubjectKind.SERVICE if actor.is_service_account else SubjectKind.HUMAN,
            solo_mode=is_solo,
        )

    @property
    def is_human(self) -> bool:
        return self.kind is SubjectKind.HUMAN


class QuotaCeilings(Protocol):
    max_compute_minutes: int
    max_model_cost_usd: float


class QuotaLookup(Protocol):
    """Section 9.7: a plan's budget is capped by what the project is allowed to spend."""

    def for_project(self, project_id: str) -> QuotaCeilings: ...


@dataclass
class TransitionResult:
    event: RunEvent
    state: RunState
    replayed: bool = False
    escalated: bool = False
    reasons: list[str] = field(default_factory=list)
    tolerance_exhausted: bool = False


@dataclass(frozen=True)
class GateRequestResult:
    """A gate ask, resolved against the index.

    Gates are subject-addressed, so asking twice for the same digest does not open a second one: the
    row that already answers is the one handed back, whether or not this call created it (§9.2). A
    caller must never be shown a freshly built pending object while the index holds a decided row.
    """

    approval: Approval
    replayed: bool


class Orchestrator:
    def __init__(
        self,
        catalog: Catalog,
        *,
        policy: ApprovalPolicy,
        quotas: QuotaLookup,
        clock=None,
    ) -> None:
        self.catalog = catalog
        self.policy = policy
        self.quotas = quotas
        self.clock = clock or catalog.clock

    # ------------------------------------------------------------------ reads

    def state_of(self, run_id: str) -> RunState:
        return RunState(self.catalog.run_state(run_id))

    def is_working(self, run_id: str) -> bool:
        """Section 9.2: spend, retries and progress are accepted only while the run is on the chain.

        A run parked in a branch waits for a person; the workflow engine has to know that before it
        executes nodes into a state that would reject their usage events.
        """
        return is_main_chain(self.state_of(run_id))

    def journal(self, run_id: str, *, kind: Optional[str] = None, limit=None, offset=0) -> list[RunEvent]:
        return self.catalog.list_run_events(run_id, kind=kind, limit=limit, offset=offset)

    def usage(self, run_id: str) -> LoopUsage:
        minutes, cost = self.catalog.usage_totals(run_id)
        return LoopUsage(iterations=self.catalog.loop_iterations(run_id), compute_minutes=minutes, model_cost_usd=cost)

    def ceiling(self, run_id: str, plan: Optional[AnalysisPlan] = None) -> LoopCeiling:
        manifest = self.catalog.get_run(run_id)
        return LoopCeiling.of(
            (plan or self.plan_of(run_id)).loop_budget,
            quota_compute_minutes=self.quotas.for_project(manifest.project_id).max_compute_minutes,
            quota_model_cost_usd=self.quotas.for_project(manifest.project_id).max_model_cost_usd,
        )

    def plan_of(self, run_id: str) -> AnalysisPlan:
        manifest = self.catalog.get_run(run_id)
        return self.catalog.get_plan(manifest.plan_id, manifest.plan_version)

    def _node(self, plan: AnalysisPlan, node_id: str) -> PlanNode:
        node = next((n for n in plan.nodes if n.node_id == node_id), None)
        if node is None:
            raise LookupError(f"node {node_id} is not in plan {plan.plan_id}@{plan.plan_version}")
        return node

    def _resume_state(self, run_id: str, current: RunState) -> Optional[RunState]:
        """The state a branch was entered from: the only place a waiting run may return to."""
        entry = self.catalog.last_event_into_state(run_id, str(current), kind=str(RunEventKind.STATE))
        return entry.from_state if entry is not None else None

    def _versions(self, manifest: RunManifest) -> dict[str, Any]:
        return {
            "plan_id": manifest.plan_id,
            "plan_version": manifest.plan_version,
            "tool_registry_snapshot": manifest.tool_registry_snapshot,
            "tool_versions": manifest.tool_versions,
        }

    # ------------------------------------------------------------------ approvals

    def request_approval(
        self,
        run_id: str,
        kind: ApprovalKind,
        *,
        actor: ActorRef,
        node_id: Optional[str] = None,
        assigned_to: Optional[str] = None,
        plan_version: Optional[str] = None,
    ) -> GateRequestResult:
        manifest = self.catalog.get_run(run_id)
        digest = self._subject_digest(run_id, kind, node_id=node_id, plan_version=plan_version)
        approver = assigned_to or self.catalog.get_project().created_by
        now = self.clock()
        approval = Approval(
            approval_id=new_approval_id(run_id, kind, digest, node_id),
            run_id=run_id,
            kind=kind,
            subject_digest=digest,
            requested_by=actor.subject_id,
            assigned_to=approver,
            requested_at=now,
            sla_at=self.policy.deadline(now),
            node_id=node_id,
            solo_mode=actor.solo_mode,
        )
        replayed = self.catalog.approval_exists(approval.approval_id)
        self.catalog.record_approval(approval)
        return GateRequestResult(approval=self.catalog.get_approval(approval.approval_id), replayed=replayed)

    def _subject_digest(
        self,
        run_id: str,
        kind: ApprovalKind,
        *,
        node_id: Optional[str],
        plan_version: Optional[str] = None,
    ) -> str:
        manifest = self.catalog.get_run(run_id)
        if kind in (ApprovalKind.PLAN, ApprovalKind.ESCALATION):
            return self.catalog.plan_digest(manifest.plan_id, plan_version or manifest.plan_version)
        if node_id is None:
            raise ValueError(f"a {kind} approval names the node it gates")
        plan = self.plan_of(run_id)
        if plan_version is not None and plan_version != plan.plan_version:
            raise ValueError("a node approval covers the node in the plan the run is executing")
        return node_subject_digest(self._node(plan, node_id))

    def decide_approval(self, approval_id: str, *, actor: ActorRef, approved: bool, reason: str) -> Approval:
        approval = self.catalog.get_approval(approval_id)
        assert_decidable(approval, actor_id=actor.subject_id, actor_kind=actor.kind, solo_mode=actor.solo_mode)
        return self.catalog.decide_approval(
            approval_id,
            state=str(ApprovalState.APPROVED if approved else ApprovalState.REJECTED),
            decided_by=actor.subject_id,
            decided_at=self.clock(),
            reason=reason,
            read_by=approval.assigned_to,
        )

    def delegate_approval(self, approval_id: str, *, actor: ActorRef, to_subject: str) -> Approval:
        approval = self.catalog.get_approval(approval_id)
        assert_delegatable(approval, actor_id=actor.subject_id, actor_kind=actor.kind, to_subject=to_subject)
        return self.catalog.delegate_approval(
            approval_id,
            from_subject=actor.subject_id,
            to_subject=to_subject,
            at=self.clock(),
            read_by=approval.assigned_to,
        )

    def approval_of(self, approval_id: str) -> Approval:
        return self.catalog.get_approval(approval_id)

    def pending_approvals(
        self, *, run_id: Optional[str] = None, state: Optional[ApprovalState] = None, limit=None, offset=0
    ) -> list[Approval]:
        return self.catalog.list_approvals(
            state=str(state) if state else None, run_id=run_id, limit=limit, offset=offset
        )

    def overdue_approvals(
        self, *, now: Optional[datetime] = None, limit: Optional[int] = None, offset: int = 0
    ) -> list[Approval]:
        """Section 9.2: the escalation reminder is a read over lapsed windows, not a stored event.

        The window is compared in the index rather than in Python, so an inbox with thousands of open
        gates costs one page of rows. ``is_overdue`` and the ``due_before`` predicate say the same
        thing - pending and ``sla_at`` strictly past - because every stamp in the column comes from
        this deployment's clock, which is what makes the textual comparison chronological.
        """
        moment = now or self.clock()
        return self.catalog.list_approvals(
            state=str(ApprovalState.PENDING), due_before=moment, limit=limit, offset=offset
        )

    def granted_for(self, run_id: str, kind: ApprovalKind, *, node_id: Optional[str] = None) -> Optional[Approval]:
        digest = self._subject_digest(run_id, kind, node_id=node_id)
        return self.catalog.find_approval(
            run_id=run_id, kind=str(kind), subject_digest=digest, node_id=node_id
        )

    def _require_grant(
        self, run_id: str, kind: ApprovalKind, *, node_id: Optional[str] = None, approval_id: Optional[str] = None
    ) -> Approval:
        digest = self._subject_digest(run_id, kind, node_id=node_id)
        if approval_id is not None:
            approval = self.catalog.get_approval(approval_id)
            if approval.run_id != run_id or approval.kind is not kind or approval.subject_digest != digest:
                raise ApprovalRequired(
                    f"approval {approval_id} does not cover {kind} of run {run_id} as it stands now",
                    field="approval_id",
                )
        else:
            found = self.catalog.find_approval(
                run_id=run_id, kind=str(kind), subject_digest=digest, node_id=node_id
            )
            if found is None:
                raise ApprovalRequired(
                    f"{kind} approval for run {run_id} has not been requested; "
                    f"its id will be {new_approval_id(run_id, kind, digest, node_id)}",
                    field="approval_id",
                )
            approval = found
        if approval.state is ApprovalState.REJECTED:
            raise ApprovalRequired(
                f"the {kind} approval for this subject was rejected: {approval.decision_reason}"
            )
        if not approval.is_granted():
            raise ApprovalRequired(
                f"{kind} approval {approval.approval_id} is {approval.state}; a human decision is required"
            )
        return approval

    def require_node_approval(self, run_id: str, node_id: str) -> Approval:
        """Section 9.6: the workflow engine calls this before it runs a gated node."""
        node = self._node(self.plan_of(run_id), node_id)
        if not node.requires_approval:
            raise ValueError(f"node {node_id} needs no approval")
        return self._require_grant(run_id, ApprovalKind.NODE, node_id=node_id)

    # ------------------------------------------------------------------ transitions

    def transition(
        self,
        run_id: str,
        target: RunState,
        *,
        actor: ActorRef,
        reason: str,
        attempt: int = 1,
        from_state: Optional[RunState] = None,
        approval_id: Optional[str] = None,
        input_hash: Optional[str] = None,
        resume_with_plan_version: Optional[str] = None,
    ) -> TransitionResult:
        manifest = self.catalog.get_run(run_id)
        current = self.state_of(run_id)
        plan = self.plan_of(run_id)
        resume_to = self._resume_state(run_id, current)

        # Section 9.2: a callback that arrives twice is recognised before anything else, so the second
        # delivery reports the first one's result instead of failing on a state that has since moved.
        # A loop pass is identified by its iteration: the edge repeats, so the attempt alone cannot tell
        # pass 3 from the duplicate delivery of pass 2.
        used = self.usage(run_id) if is_loop_iteration(target) else None
        iteration = used.iterations + 1 if used is not None else 0
        claimed = from_state or current
        replay_id = run_event_id(
            run_id,
            RunEventKind.STATE,
            node_id=None,
            attempt=attempt,
            from_state=claimed,
            to_state=target,
            loop_iteration=iteration,
        )
        if self.catalog.has_run_event(replay_id):
            stored = self.catalog.get_run_event(replay_id)
            return TransitionResult(event=stored, state=stored.to_state, replayed=True)
        if claimed is not current:
            raise IllegalTransition(
                f"run {run_id} is in {current}; the callback claims it was in {claimed}",
                run_state=current,
            )

        if target not in legal_targets(current, resume_to=resume_to):
            raise IllegalTransition(
                f"run {run_id} cannot go {current} -> {target}; from {current} the plan allows "
                f"{sorted(str(t) for t in legal_targets(current, resume_to=resume_to))}",
                run_state=current,
            )
        if target in HUMAN_ONLY_TARGETS and not actor.is_human:
            raise ActorNotAllowed(
                f"only a person may move a run to {target}: an agent may not end a run or declare "
                "its deliverable ready (section 9.1)",
                run_state=current,
            )
        if target in APPROVAL_GATED_TARGETS:
            self._require_grant(run_id, ApprovalKind.PLAN, approval_id=approval_id)
        if current is RunState.ESCALATED_TO_HUMAN and target is resume_to:
            plan = self._authorised_resumption(
                run_id, approval_id=approval_id, plan_version=resume_with_plan_version
            )
        if target is RunState.REPORT_READY:
            self._require_all_node_grants(run_id, plan)

        if is_loop_iteration(target):
            reasons = self.ceiling(run_id, plan).exhausted(
                LoopUsage(
                    iterations=iteration,
                    compute_minutes=used.compute_minutes,
                    model_cost_usd=used.model_cost_usd,
                )
            )
            if reasons:
                return self._escalate(run_id, actor=actor, reasons=reasons, attempt=attempt)

        event = self._event(
            run_id,
            RunEventKind.STATE,
            from_state=current,
            to_state=target,
            actor=actor,
            reason=reason,
            attempt=attempt,
            input_hash=input_hash or sha256_of_json(manifest.input_hashes),
            versions=self._versions(manifest),
            loop_iteration=iteration,
        )
        applied = self.catalog.apply_transition(
            event, finished_at=event.at if target in TERMINAL else None
        )
        if not applied:
            stored = self.catalog.get_run_event(event.event_id)
            return TransitionResult(event=stored, state=stored.to_state, replayed=True)
        return TransitionResult(event=event, state=target)

    def cancel(self, run_id: str, *, actor: ActorRef, reason: str) -> TransitionResult:
        return self.transition(run_id, RunState.FAILED, actor=actor, reason=reason)

    def _escalate(self, run_id: str, *, actor: ActorRef, reasons: list[str], attempt: int) -> TransitionResult:
        """Section 9.2: reaching the ceiling does not fail the run, it hands it to a person."""
        current = self.state_of(run_id)
        manifest = self.catalog.get_run(run_id)
        event = self._event(
            run_id,
            RunEventKind.STATE,
            from_state=current,
            to_state=RunState.ESCALATED_TO_HUMAN,
            actor=actor,
            reason="targeted-analysis loop reached its ceiling: " + "; ".join(reasons),
            attempt=attempt,
            input_hash=sha256_of_json(manifest.input_hashes),
            versions=self._versions(manifest),
        )
        self.catalog.apply_transition(event)
        return TransitionResult(
            event=event, state=RunState.ESCALATED_TO_HUMAN, escalated=True, reasons=reasons
        )

    def _authorised_resumption(
        self, run_id: str, *, approval_id: Optional[str], plan_version: Optional[str]
    ) -> AnalysisPlan:
        """Resuming an escalated run needs a human approval of a plan version with more room.

        Without the second plan version a person could restart the loop indefinitely by clicking the
        same approval again; a ceiling that only ever grows is what makes the restart bounded.
        """
        manifest = self.catalog.get_run(run_id)
        previous = self.plan_of(run_id)
        if approval_id is None:
            raise ApprovalRequired(
                f"resuming {RunState.ESCALATED_TO_HUMAN} needs an escalation approval naming the plan "
                "version to run next",
                field="approval_id",
            )
        if plan_version is None:
            raise ApprovalRequired(
                "resuming an escalated run names the plan version its budget was raised in",
                field="plan_version",
            )
        approval = self.catalog.get_approval(approval_id)
        if approval.kind is not ApprovalKind.ESCALATION or approval.run_id != run_id:
            raise ApprovalRequired(
                f"approval {approval_id} is a {approval.kind} approval of run {approval.run_id}, "
                "not an escalation of this run",
                field="approval_id",
            )
        if not approval.is_granted():
            raise ApprovalRequired(f"escalation approval {approval_id} is {approval.state}")
        try:
            replacement = self.catalog.get_plan(manifest.plan_id, plan_version)
        except LookupError:
            raise ApprovalRequired(
                f"plan {manifest.plan_id}@{plan_version} is not recorded; raise the budget in a new "
                "plan version first",
                field="plan_version",
            ) from None
        if approval.subject_digest != self.catalog.plan_digest(manifest.plan_id, plan_version):
            raise ApprovalRequired(
                f"escalation approval {approval_id} was granted over a different plan version than "
                f"{manifest.plan_id}@{plan_version}",
                field="approval_id",
            )
        refusals = invalid_resumption_budget(previous.loop_budget, replacement.loop_budget)
        if refusals:
            raise ApprovalRequired(
                f"plan {manifest.plan_id}@{plan_version} cannot authorise a restart: " + "; ".join(refusals),
                field="plan_version",
            )
        self.catalog.set_run_plan(
            run_id, plan_version=plan_version, tool_versions={n.tool_name: n.tool_version for n in replacement.nodes}
        )
        return replacement

    def _require_all_node_grants(self, run_id: str, plan: AnalysisPlan) -> None:
        missing = [
            node.node_id
            for node in plan.nodes
            if node.requires_approval
            and not (
                (grant := self.granted_for(run_id, ApprovalKind.NODE, node_id=node.node_id)) is not None
                and grant.is_granted()
            )
        ]
        if missing:
            raise ApprovalRequired(
                f"the deliverable cannot be declared ready while gated nodes have no approval: {missing}",
                field="node_id",
            )

    # ------------------------------------------------------------------ retries and usage

    def register_node_retry(
        self, run_id: str, node_id: str, *, attempt: int, actor: ActorRef, reason: str
    ) -> TransitionResult:
        current = self.state_of(run_id)
        if not is_main_chain(current):
            raise IllegalTransition(
                f"a node-level retry is reported while the run is working, not while it is {current}",
                run_state=current,
            )
        node = self._node(self.plan_of(run_id), node_id)
        assert_attempt_within_policy(node.retry_policy, attempt, kind=f"node {node_id} retry")
        event = self._event(
            run_id,
            RunEventKind.NODE_RETRY,
            from_state=current,
            to_state=current,
            actor=actor,
            reason=reason,
            attempt=attempt,
            node_id=node_id,
            input_hash=sha256_hex(node_id.encode()),
        )
        applied = self.catalog.append_run_event(event)
        if not applied:
            event = self.catalog.get_run_event(event.event_id)
        return TransitionResult(
            event=event,
            state=current,
            replayed=not applied,
            tolerance_exhausted=attempt >= node.retry_policy.max_attempts,
        )

    def business_retry(
        self,
        run_id: str,
        node_id: str,
        *,
        attempt: int,
        actor: ActorRef,
        reason: str,
        approval_id: Optional[str] = None,
    ) -> TransitionResult:
        """The state machine's own retry: it restarts the step the run stopped on, after the backoff."""
        current = self.state_of(run_id)
        if current is not RunState.RETRYABLE_FAILURE:
            raise IllegalTransition(
                f"a business retry restarts a run in RETRYABLE_FAILURE, not one in {current}",
                run_state=current,
            )
        resume_to = self._resume_state(run_id, current)
        if resume_to is None:
            raise IllegalTransition(
                f"run {run_id} is in RETRYABLE_FAILURE with no recorded state to return to",
                run_state=current,
            )
        node = self._node(self.plan_of(run_id), node_id)
        assert_attempt_within_policy(node.retry_policy, attempt, kind=f"business retry of node {node_id}")
        last = self.catalog.last_node_event(run_id, str(RunEventKind.BUSINESS_RETRY), node_id)
        entry = self.catalog.last_event_into_state(run_id, str(current))
        last_at = max(filter(None, [last.at if last else None, entry.at if entry else None]), default=None)
        assert_backoff_elapsed(node.retry_policy, last_at, self.clock(), kind=f"node {node_id} retry")

        event = self._event(
            run_id,
            RunEventKind.BUSINESS_RETRY,
            from_state=current,
            to_state=current,
            actor=actor,
            reason=reason,
            attempt=attempt,
            node_id=node_id,
            input_hash=sha256_hex(node_id.encode()),
        )
        self.catalog.append_run_event(event)
        return self.transition(
            run_id, resume_to, actor=actor, reason=reason, attempt=attempt, approval_id=approval_id
        )

    def report_usage(
        self,
        run_id: str,
        node_id: str,
        *,
        compute_minutes: float = 0.0,
        model_cost_usd: float = 0.0,
        attempt: int = 1,
        actor: ActorRef,
        reason: str = "work reported by the workflow engine",
    ) -> TransitionResult:
        current = self.state_of(run_id)
        if not is_main_chain(current):
            raise IllegalTransition(f"run {run_id} is {current}; spend is reported against a working run", run_state=current)
        self._node(self.plan_of(run_id), node_id)
        event = self._event(
            run_id,
            RunEventKind.USAGE,
            from_state=current,
            to_state=current,
            actor=actor,
            reason=reason,
            attempt=attempt,
            node_id=node_id,
            input_hash=sha256_hex(f"{node_id}|{compute_minutes}|{model_cost_usd}".encode()),
            compute_minutes=compute_minutes,
            model_cost_usd=model_cost_usd,
        )
        applied = self.catalog.append_run_event(event)
        reasons = self.ceiling(run_id).exhausted(self.usage(run_id))
        if reasons and applied:
            return self._escalate(run_id, actor=actor, reasons=reasons, attempt=attempt)
        if reasons:
            # A replayed report must not escalate twice; say what the ceiling says instead.
            return TransitionResult(
                event=self.catalog.get_run_event(event.event_id),
                state=current,
                replayed=True,
                reasons=reasons,
            )
        return TransitionResult(event=event, state=current, replayed=not applied)

    # ------------------------------------------------------------------ event building

    def _event(
        self,
        run_id: str,
        kind: RunEventKind,
        *,
        from_state: RunState,
        to_state: RunState,
        actor: ActorRef,
        reason: str,
        attempt: int,
        input_hash: str,
        versions: Optional[dict[str, Any]] = None,
        node_id: Optional[str] = None,
        loop_iteration: int = 0,
        compute_minutes: float = 0.0,
        model_cost_usd: float = 0.0,
    ) -> RunEvent:
        at = self.clock()
        return RunEvent(
            event_id=run_event_id(
                run_id,
                kind,
                node_id=node_id,
                attempt=attempt,
                from_state=from_state,
                to_state=to_state,
                loop_iteration=loop_iteration,
            ),
            run_id=run_id,
            kind=kind,
            from_state=from_state,
            to_state=to_state,
            actor_id=actor.subject_id,
            actor_kind=actor.kind,
            reason=reason,
            input_hash=input_hash,
            versions=versions or {},
            attempt=attempt,
            node_id=node_id,
            loop_iteration=loop_iteration,
            compute_minutes=compute_minutes,
            model_cost_usd=model_cost_usd,
            at=at,
        )
