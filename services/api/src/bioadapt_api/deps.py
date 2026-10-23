"""Request wiring: identity, project scope, authorization, audit (plan V2.0 sections 9.7, 11.2, 13.1).

Project-scoped routes run inside a :class:`Session` that has already resolved the caller, opened a
catalog connection scoped to one project (the row level security predicate), and records one audit row
per authorization decision - allowed or denied.
"""

from __future__ import annotations

import bisect
import threading
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from functools import cached_property
from typing import Annotated, Any, Callable, Literal, Optional, Protocol

from fastapi import Depends, Request

from bioadapt_evidence import EvidenceCardService
from bioadapt_orchestrator.approvals import ApprovalPolicy
from bioadapt_orchestrator.service import Orchestrator
from bioadapt_policies.access import Action, AccessDecision, Actor, Resource, authorize
from bioadapt_policies.providers import ProviderTerms
from bioadapt_schemas.enums import PrivacyTier, UsageScope
from bioadapt_schemas.thresholds import Thresholds
from bioadapt_storage.catalog import Catalog
from bioadapt_storage.objectstore import ObjectStore, Signer
from bioadapt_storage.traces import TraceStore
from bioadapt_tool_registry import DEFAULT_ADHOC_LIMITS, AdhocSandboxLimits

from .errors import forbidden, invalid, unauthorized
from .identity import IdTokenClaims, TokenVerifier
from .login_keys import Ed25519Verifier
from .observability import LogSink, new_trace_id
from .quota import QuotaProvider


class CatalogProvider(Protocol):
    """Production hands out one connection per request with ``app.project_id`` set; never a shared session."""

    def acquire(self, project_id: str) -> Catalog: ...


class SingleNodeCatalogs:
    """Reference provider: one catalog connection, one scoped session per request.

    The connection is shared, so a scope left on it is another request's; ``session`` re-binds this
    request's project on every call instead, which also survives the threadpool hop between a route's
    dependencies and its body. A multi-worker deployment installs a provider that hands out a connection
    per request and sets ``app.project_id`` on it.
    """

    def __init__(self, catalog: Catalog) -> None:
        self.catalog = catalog

    def acquire(self, project_id: str) -> Catalog:
        return self.catalog.session(project_id)  # type: ignore[return-value]


class Queue(Protocol):
    def submit(self, run_id: str, *, priority: int = 5) -> Optional[int]: ...

    def position(self, run_id: str) -> Optional[int]: ...

    def finish(self, run_id: str) -> Optional[str]: ...


class InMemoryQueue:
    """Reference scheduler behind the ``202 + run_id`` contract; the orchestrator replaces it in stage 5.

    It hands out one slot per live run and takes it back when a transition reaches a terminal state, so a
    second run's ``queue_position`` moves instead of waiting forever. Nothing here starts a run: the
    reference build has no worker, so an admitted run stays where its engine left it.
    """

    def __init__(self, *, running_slots: int = 1) -> None:
        self.running_slots = running_slots
        self._running: list[str] = []
        self._pending: list[tuple[int, int, str]] = []
        self._seq = 0
        self._lock = threading.RLock()

    def submit(self, run_id: str, *, priority: int = 5) -> int:
        with self._lock:
            if len(self._running) < self.running_slots:
                self._running.append(run_id)
                return 0
            self._seq += 1
            bisect.insort(self._pending, (-priority, self._seq, run_id))
            return self.position(run_id) or 0

    def position(self, run_id: str) -> Optional[int]:
        with self._lock:
            if run_id in self._running:
                return 0
            for index, (_neg, _seq, pending) in enumerate(self._pending):
                if pending == run_id:
                    return index + 1
            return None

    def finish(self, run_id: str) -> Optional[str]:
        """Free the slot and admit the next run; returns the admitted run id."""
        with self._lock:
            if run_id in self._running:
                self._running.remove(run_id)
            self._pending = [item for item in self._pending if item[2] != run_id]
            if self._running or not self._pending:
                return None
            admitted = self._pending.pop(0)[2]
            self._running.append(admitted)
            return admitted


@dataclass(frozen=True)
class DeploymentCapabilities:
    """Explicit deployment declarations, not health checks or authorization switches."""

    mode: Literal["reference", "configured", "unknown"] = "unknown"
    object_upload: Literal["configured", "unavailable", "unknown"] = "unknown"
    run_execution: Literal["configured", "unavailable", "unknown"] = "unknown"
    export_generation: Literal["configured", "unavailable", "unknown"] = "unknown"


@dataclass
class ApiState:
    catalogs: CatalogProvider
    objects: ObjectStore
    signer: Signer
    verifier: TokenVerifier
    quotas: QuotaProvider
    queue: Queue
    traces: TraceStore
    log_sink: LogSink
    clock: Callable[[], datetime]
    #: Section 9.2 + appendix C1: the approval window is a deployment input, never a code constant.
    approval_policy: ApprovalPolicy
    local_hosts: frozenset[str] = frozenset()
    cloud_allowlist: frozenset[str] = frozenset()
    provider_terms: Mapping[str, ProviderTerms] = field(default_factory=dict)
    stream_max_events: int = 50
    stream_poll_seconds: float = 0.05
    #: Section 9.6: how long one ad-hoc grant stays executable, and the cage its tool has to fit. Both are
    #: deployment inputs: the engine that later hydrates a registry from these rows has to be answering the
    #: same question the API was when it wrote them, and a second copy in code could drift from the first.
    adhoc_grant_ttl: timedelta = timedelta(hours=24)
    adhoc_limits: AdhocSandboxLimits = DEFAULT_ADHOC_LIMITS
    deployment: DeploymentCapabilities = field(default_factory=DeploymentCapabilities)
    #: Section 11.2: the login-key directory, when this deployment runs on key files instead of a printed
    #: token. None is not "no keys yet", it is "no way to check one", and it closes the exchange route.
    login_keys: Optional[Ed25519Verifier] = None
    #: How long a token bought at the exchange stays valid. Minutes, deliberately: the key file is the
    #: durable thing a person keeps, and this is all a copied bearer token is worth.
    login_token_ttl: timedelta = timedelta(minutes=15)
    #: The credential this control plane presents to each declared node, keyed by node id. Per node,
    #: not one secret for all of them: a secret every node knows opens every node. Deployment-side
    #: like every other secret - it belongs in the process's environment, never in a row of the node
    #: registry (§9.7). Empty means the deployment cannot open its nodes, and dispatch refuses rather
    #: than running work somewhere the run was not placed.
    node_tokens: Mapping[str, str] = field(default_factory=dict)


@dataclass
class Session:
    state: ApiState
    catalog: Catalog
    actor: Actor
    project_id: str
    trace_id: str

    @property
    def clock(self):
        return self.state.clock

    @cached_property
    def orchestrator(self) -> Orchestrator:
        """One state machine per request: the same catalog scope, policy and clock the route runs under."""
        return Orchestrator(
            self.catalog, policy=self.state.approval_policy, quotas=self.state.quotas, clock=self.state.clock
        )

    @cached_property
    def thresholds(self) -> Thresholds:
        """Section 7.3: the cut set this project's numbers are judged against is the one it registered.

        It lives on the project row, not on the deployment, because a card cites the version it was
        measured under - a second copy here would let the two disagree without either side noticing.
        """
        return self.catalog.get_project().thresholds

    def require_measured_cuts(self, action: Action, resource_id: str) -> None:
        """Section 1.5 + 14.4: a conclusion leaves the platform only over a cut set somebody measured.

        Stage 0 may compute, draft and queue against the appendix C1 placeholders - running that baseline
        is how the numbers get replaced - but sign-off and export are the two exits where an unconfirmed
        cut would read as a finding, so they are the two that refuse it.
        """
        thresholds = self.thresholds
        if not thresholds.is_unconfirmed_default_set:
            return
        denial = (
            f"{action} is closed while this project judges against threshold set {thresholds.version!r}, "
            "which no person has confirmed as measured"
        )
        self.audit(
            str(action),
            resource_id,
            details={"allowed": False, "denials": [denial], "thresholds_version": thresholds.version},
        )
        raise forbidden([denial], resource_id)

    def evidence_cards(self, run_id: str) -> EvidenceCardService:
        """The drafting path for one run: same project scope, object plane and cut set as this request.

        Section 9.1 keeps this a *drafting* dependency only - it can file an unknown-hypothesis card and
        refuse to touch one a person has acted on, and it holds no model, approval or egress authority.
        """
        return EvidenceCardService(
            catalog=self.catalog, objects=self.state.objects, run_id=run_id, thresholds=self.thresholds
        )

    def subject_kind(self) -> str:
        return "service" if self.actor.is_service_account else "human"

    def enforce(
        self,
        action: Action,
        resource_id: str,
        resource_type: str,
        *,
        privacy_tier: Optional[PrivacyTier] = None,
        usage_scope: UsageScope = UsageScope.THIS_PROJECT_ONLY,
        attributes: Optional[dict[str, Any]] = None,
        requesting_subject_id: Optional[str] = None,
        project_id: Optional[str] = None,
    ) -> AccessDecision:
        resource = Resource(
            resource_id=resource_id,
            resource_type=resource_type,
            project_id=project_id or self.project_id,
            privacy_tier=privacy_tier,
            usage_scope=usage_scope,
            attributes=attributes or {},
        )
        decision = authorize(self.actor, action, resource, requesting_subject_id=requesting_subject_id)
        self.audit(
            str(action),
            resource_id,
            decision_id=decision.decision_id(),
            details={"allowed": decision.allowed, "denials": decision.denials, "policy_version": decision.policy_version},
        )
        if not decision.allowed:
            raise forbidden(decision.denials, resource_id)
        return decision

    def audit(
        self, action: str, resource_id: str, *, decision_id: Optional[str] = None, details: Optional[dict[str, Any]] = None
    ) -> str:
        return self.catalog.audit(
            subject_id=self.actor.subject_id,
            subject_kind=self.subject_kind(),
            action=action,
            resource_id=resource_id,
            decision_id=decision_id,
            trace_id=self.trace_id,
            details=details or {},
        )


def get_state(request: Request) -> ApiState:
    return request.app.state.api


def get_claims(request: Request, state: ApiState = Depends(get_state)) -> IdTokenClaims:
    scheme, _, token = request.headers.get("authorization", "").partition(" ")
    if scheme.lower() != "bearer" or not token.strip():
        raise unauthorized()
    return state.verifier.verify(token.strip(), now=state.clock())


def open_session(state: ApiState, claims: IdTokenClaims, project_id: str, trace_id: str) -> Session:
    return Session(
        state=state,
        catalog=state.catalogs.acquire(project_id),
        actor=claims.actor(),
        project_id=project_id,
        trace_id=trace_id,
    )


def request_trace_id(request: Request) -> str:
    return getattr(request.state, "trace_id", new_trace_id())


def get_session(
    request: Request,
    claims: IdTokenClaims = Depends(get_claims),
    state: ApiState = Depends(get_state),
) -> Session:
    project_id = request.path_params.get("project_id")
    if not project_id:
        raise invalid("project_id", "this endpoint is project scoped and needs a project in the path")
    return open_session(state, claims, project_id, request_trace_id(request))


StateDep = Annotated[ApiState, Depends(get_state)]
ClaimsDep = Annotated[IdTokenClaims, Depends(get_claims)]
SessionDep = Annotated[Session, Depends(get_session)]
