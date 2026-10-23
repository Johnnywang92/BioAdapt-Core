"""The run driver: a plan's DAG executed against the index, the registry and the reference wrapper.

The orchestrator is injected rather than imported (§9.5): turning nodes into assets is this module's
job, deciding whether a run may move is the state machine's, and ``RunJournal`` below is the whole of
the interface between them. Three rules decide the shape of the loop:

* a gated node runs only when the digest-addressed approval the journal holds for it says approved
  (§9.6), and an ad-hoc tool's grant is *consumed*, so one approval buys exactly one execution;
* a node whose checkpoint matches these exact inputs, parameters and registry snapshot is not
  recomputed (§4.1) - its assets are read back from the index, never replayed from a state blob;
* every attempt reports what it cost, so §9.2's ceiling can escalate the run in the middle of a plan
  rather than after it, because reaching a ceiling is a hand-off to a person and not a failure.

A workflow reason is never an exception here. A missing approval, a spent ad-hoc grant, an unwired
input or a broken asset comes back on the ``RunOutcome`` together with the state the run was moved
to, so the caller reports what happened instead of guessing from a traceback.
"""

from __future__ import annotations

import json
import time
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any, Callable, Mapping, Optional, Protocol, Sequence, Union

from bioadapt_schemas.entities import AssetLineage, DataAsset, RunManifest, sha256_of_json
from bioadapt_schemas.enums import (
    ApprovalKind,
    ApprovalState,
    AssetRole,
    FilteringState,
    PrivacyTier,
    RetentionClass,
    RunState,
    SubjectKind,
    ToolClass,
    UsageScope,
)
from bioadapt_schemas.errors import ErrorClass, to_run_state
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_schemas.orchestration import Approval, ExecutionReceipt, RunCheckpoint, node_approval_id
from bioadapt_schemas.plan import AnalysisPlan, PlanNode
from bioadapt_storage.catalog import Catalog, ExecutionConflictError
from bioadapt_storage.objectstore import ObjectStore, object_key
from bioadapt_tool_registry.enums import NetworkMode, ToolResultStatus
from bioadapt_tool_registry.execution import (
    ExecutionBackend,
    ExecutionCage,
    ExecutionInput,
    ExecutionOutcome,
    ExecutionRequest,
    outcome_digest,
)
from bioadapt_tool_registry.registry import (
    DEFAULT_ADHOC_LIMITS,
    AdhocExhaustedError,
    ForbiddenToolError,
    ToolRegistry,
    UnknownToolError,
)
from bioadapt_tool_registry.result import ToolResult
from bioadapt_tool_registry.spec import ToolSpec

from .backends import InProcessBackend
from .implementation import (
    TASK_INPUT_SLOTS,
    TASK_RUNNERS,
    parameters_sha256,
    required_slots,
)
from .products import Product, read_product

#: The reference products are canonical JSON, so the encoding is the platform's own rather than the
#: caller's: an asset's identity is its bytes, and the format only names how they are written.
ASSET_FORMAT = "json"
URI_SCHEME = "s3://"


class IntegrityError(RuntimeError):
    """The stored bytes and the index row do not describe the same asset."""


class Actor(Protocol):
    """The subject the journal sees. ``bioadapt_orchestrator.service.ActorRef`` satisfies this."""

    @property
    def subject_id(self) -> str: ...

    @property
    def kind(self) -> SubjectKind: ...


class RunJournal(Protocol):
    """The part of the orchestrator the driver uses, and the whole of its dependency on that service."""

    def state_of(self, run_id: str) -> RunState: ...

    def is_working(self, run_id: str) -> bool:
        """Whether the machine will accept this run's usage events and retries right now."""
        ...

    def plan_of(self, run_id: str) -> AnalysisPlan: ...

    def waits_before(self, run_id: str, from_state: RunState, to_state: RunState) -> int:
        """How many times this run has already parked on this edge."""
        ...

    def granted_for(
        self, run_id: str, kind: ApprovalKind, *, node_id: Optional[str] = None
    ) -> Optional[Approval]: ...

    def register_node_retry(
        self, run_id: str, node_id: str, *, attempt: int, actor: Actor, reason: str
    ) -> Any: ...

    def report_usage(
        self,
        run_id: str,
        node_id: str,
        *,
        compute_minutes: float,
        model_cost_usd: float,
        attempt: int,
        actor: Actor,
        reason: str,
    ) -> Any: ...

    def transition(
        self, run_id: str, target: RunState, *, actor: Actor, reason: str, attempt: int = 1
    ) -> Any: ...


@dataclass(frozen=True)
class AssetDefaults:
    """How the driver labels the assets a node produced.

    Retention and sharing scope are project decisions (§5.8, §5.9), so they arrive here rather than
    being inferred from what a step happens to be. ``ephemeral_slots`` marks the intermediates a rerun
    can recompute, which keeps a long run's storage bounded without touching the deliverables.
    """

    privacy_tier: PrivacyTier
    usage_scope: UsageScope
    retention_class: RetentionClass
    filtering_state: FilteringState = FilteringState.NONE
    ephemeral_slots: frozenset[str] = frozenset()

    def retention(self, slot: str) -> RetentionClass:
        return RetentionClass.EPHEMERAL if slot in self.ephemeral_slots else self.retention_class


@dataclass(frozen=True)
class NodeRun:
    """One node's worth of work, including the attempts its retry policy spent to get there."""

    node_id: str
    task: str
    attempt: int
    status: ToolResultStatus
    result: Optional[ToolResult] = None
    #: slot -> asset id, for the outputs this node put on the run's wires.
    outputs: Mapping[str, str] = field(default_factory=dict)
    reused_checkpoint: bool = False
    #: §3.3: the attempt had already answered, and its products were read back from the index
    #: instead of being produced again.
    reused_receipt: bool = False
    compute_minutes: float = 0.0
    reason: Optional[str] = None


@dataclass(frozen=True)
class RunOutcome:
    run_id: str
    state: RunState
    completed: bool
    nodes: tuple[NodeRun, ...] = ()
    stopped_at: Optional[str] = None
    stopped_reason: Optional[str] = None
    #: A stop condition the driver may not act on itself: ending a run is a human decision (§9.1).
    requires_human_action: bool = False
    warnings: tuple[str, ...] = ()
    #: Every slot the plan names, resolved to the asset that answers it after this pass.
    assets: Mapping[str, str] = field(default_factory=dict)

    def node(self, node_id: str) -> Optional[NodeRun]:
        return next((run for run in self.nodes if run.node_id == node_id), None)

    def result_of(self, node_id: str) -> Optional[ToolResult]:
        run = self.node(node_id)
        return run.result if run is not None else None


@dataclass(frozen=True)
class _Input:
    """One input as the driver holds it: the index row, the bytes, and the product they are.

    The bytes travel to the backend rather than the product, because the machine that runs the work
    is not necessarily the machine that holds them (§4.2). The product is still read here, because a
    driver that handed over bytes it had not checked would be trusting an index row it never
    verified - and because the read is what tells the driver that the asset answers the slot the
    plan wired.
    """

    slot: str
    asset: DataAsset
    payload: bytes
    product: Product


@dataclass
class _Stop:
    """An internal refusal: the state the run belongs in, and the one sentence that says why."""

    state: RunState
    reason: str
    error_class: Optional[ErrorClass] = None
    requires_human_action: bool = False


def asset_for(
    product: Product,
    *,
    project_id: str,
    defaults: AssetDefaults,
    created_at: datetime,
    run_id: Optional[str] = None,
) -> DataAsset:
    """The index row a product deserves: named by its own bytes, labelled by the project's decisions.

    A root input is staged with this same function, so the asset a node reads is indistinguishable
    from the asset a node wrote (§5.2). Nothing about a platform asset depends on who produced it.
    """
    payload = product.json_bytes()
    file_sha256 = sha256_hex(payload)
    matrix = getattr(product, "matrix", None)
    retention = defaults.retention(product.slot)
    key = object_key(
        project_id=project_id,
        asset_id=f"as_{file_sha256[:12]}",
        file_sha256=file_sha256,
        retention_class=retention,
        extension=ASSET_FORMAT,
    )
    return DataAsset(
        asset_id=f"as_{file_sha256[:12]}",
        project_id=project_id,
        uri=f"{URI_SCHEME}{key}",
        file_sha256=file_sha256,
        content_fingerprint=product.content_fingerprint(),
        format=ASSET_FORMAT,
        schema_version=product.schema_version,
        size_bytes=len(payload),
        privacy_tier=defaults.privacy_tier,
        usage_scope=defaults.usage_scope,
        retention_class=retention,
        filtering_state=defaults.filtering_state,
        genome=matrix.genome if matrix is not None else None,
        created_by_run_id=run_id,
        created_at=created_at,
    )


def write_asset(
    catalog: Catalog,
    objects: ObjectStore,
    product: Product,
    *,
    project_id: str,
    defaults: AssetDefaults,
    created_at: datetime,
    run_id: Optional[str] = None,
) -> str:
    """Store a product and index it now, for callers handing work to the driver rather than running it."""
    asset = asset_for(
        product, project_id=project_id, defaults=defaults, created_at=created_at, run_id=run_id
    )
    objects.put(asset.uri[len(URI_SCHEME) :], product.json_bytes())
    return catalog.record_asset(asset)


def cage_for(node: PlanNode, spec: ToolSpec) -> ExecutionCage:
    """The ceiling one attempt runs inside, carried to the machine that runs it (§9.6).

    An ad-hoc tool runs inside the sandbox the registry admits it under, whatever the plan asks for:
    that sandbox is the smaller promise and the one a person signed. A registered tool runs inside
    the budget its own plan node declared, because that budget is what a person approved for this
    node. The ceiling travels with the request because a limit the control plane only remembers is a
    limit nobody enforces where the work happens.
    """
    if spec.tool_class is ToolClass.PROJECT_ADHOC:
        return ExecutionCage(
            cpu=DEFAULT_ADHOC_LIMITS.max_cpu,
            memory_mb=DEFAULT_ADHOC_LIMITS.max_memory_mb,
            max_minutes=DEFAULT_ADHOC_LIMITS.max_minutes,
            allow_network=False,
            allow_gpu=False,
        )
    return ExecutionCage(
        cpu=node.resources.cpu,
        memory_mb=node.resources.memory_gb * 1024,
        max_minutes=node.resources.max_minutes,
        allow_network=spec.network.mode is not NetworkMode.CLOSED,
        allow_gpu=node.resources.gpu > 0,
    )


class _NodeSink:
    """Writes one node's products, and only lets the index name them once the node succeeded.

    The asset id is derived from the bytes (§5.2), so a node that recomputes something that already
    exists resolves to the same asset instead of duplicating it - which is what lets the QC step hand
    on its unchanged counts without producing a second copy of a count matrix. Bytes written by an
    attempt that then failed stay unindexed: they are collectable as orphans, and a failed attempt
    never appears as somebody's output.
    """

    def __init__(
        self,
        *,
        catalog: Catalog,
        objects: ObjectStore,
        run_id: str,
        project_id: str,
        node_id: str,
        defaults: AssetDefaults,
        created_at: datetime,
    ) -> None:
        self.catalog = catalog
        self.objects = objects
        self.run_id = run_id
        self.project_id = project_id
        self.node_id = node_id
        self.defaults = defaults
        self.created_at = created_at
        self.written: dict[str, str] = {}
        self._pending: list[tuple[str, DataAsset]] = []

    def write(self, product: Product) -> str:
        asset = asset_for(
            product,
            project_id=self.project_id,
            defaults=self.defaults,
            created_at=self.created_at,
            run_id=self.run_id,
        )
        self.objects.put(asset.uri[len(URI_SCHEME) :], product.json_bytes())
        self._pending.append((product.slot, asset))
        self.written[product.slot] = asset.asset_id
        return asset.asset_id

    def commit(self) -> None:
        for ordinal, (slot, asset) in enumerate(sorted(self._pending, key=lambda item: item[0])):
            recorded = self.catalog.record_asset(asset)
            self.catalog.record_lineage(
                AssetLineage(
                    run_id=self.run_id,
                    node_id=self.node_id,
                    asset_id=recorded,
                    role=AssetRole.OUTPUT,
                    ordinal=ordinal,
                )
            )
            self.written[slot] = recorded
        self._pending = []


class WorkflowRunner:
    """Executes one run's plan nodes in dependency order and records what each one produced."""

    def __init__(
        self,
        *,
        catalog: Catalog,
        objects: ObjectStore,
        registry: ToolRegistry,
        journal: RunJournal,
        actor: Actor,
        asset_defaults: AssetDefaults,
        clock: Optional[Callable[[], datetime]] = None,
        usage_minutes: Optional[Callable[[PlanNode, ToolResult, float], float]] = None,
        backend: Optional[ExecutionBackend] = None,
    ) -> None:
        self.catalog = catalog
        self.objects = objects
        self.registry = registry
        self.journal = journal
        self.actor = actor
        self.asset_defaults = asset_defaults
        self.clock = clock or (lambda: datetime.now(UTC))
        #: How a spent attempt is charged, from the seconds the wrapper actually ran. A run's logical
        #: clock may not move during a step at all, and §9.2's ceiling counts compute, not clicks.
        self.usage_minutes = usage_minutes or (lambda node, result, elapsed: elapsed / 60.0)
        #: Where the work runs (§3.3). The default runs it in this process, which is what the
        #: platform does today; a node that runs it elsewhere answers the same seam.
        self.backend = backend or InProcessBackend()

    # ------------------------------------------------------------------ driver

    def run(self, run_id: str, *, root: Mapping[str, str]) -> RunOutcome:
        """Execute every node the plan holds, stopping at the first reason that needs someone else.

        ``root`` names the asset behind each of ``plan.root_inputs``. A node's ``inputs`` are the slot
        names its tool reads, so the plan's wires and the wrapper's slots are one vocabulary: nothing
        here has to guess which input feeds which parameter (§4.1).
        """
        entry = self.journal.state_of(run_id)
        if not self.journal.is_working(run_id):
            return self._finish(
                run_id,
                entry,
                _Stop(
                    state=entry,
                    reason=(
                        f"run {run_id} waits in {entry}: leaving a branch is the state machine's act, not "
                        "the driver's, and executing nodes into it would report usage the journal cannot "
                        "accept"
                    ),
                ),
                (),
                {},
            )
        preflight = self._preflight(run_id, root)
        if preflight is not None:
            return self._finish(run_id, entry, preflight, (), {})

        plan = self.journal.plan_of(run_id)
        manifest = self.catalog.get_run(run_id)
        wires = {slot: root[slot] for slot in sorted(plan.root_inputs)}
        self._record_lineage(run_id, "root", wires, AssetRole.INPUT)

        runs: list[NodeRun] = []
        warnings: list[str] = []
        for node in plan.topological_order():
            current = self.journal.state_of(run_id)
            if current is not entry:
                stop = _Stop(
                    state=current,
                    reason=(
                        f"the run left {entry} for {current} before node {node.node_id}; the journal "
                        "moved it, so the driver stops rather than working against a state it does not own"
                    ),
                )
                return self._finish(run_id, entry, stop, runs, wires)
            outcome: Union[NodeRun, _Stop] = self._execute_node(run_id, manifest, node, wires)
            if isinstance(outcome, _Stop):
                return self._finish(run_id, entry, outcome, runs, wires, stopped_at=node.node_id)
            runs.append(outcome)
            wires.update(outcome.outputs)
            if outcome.result is not None:
                warnings.extend(f"{node.node_id}: {warning}" for warning in outcome.result.warnings)
            if outcome.status is not ToolResultStatus.SUCCESS:
                failure = outcome.result.errors[0].error_class if outcome.result and outcome.result.errors else ErrorClass.FAILED
                return self._finish(
                    run_id,
                    entry,
                    _Stop(
                        state=to_run_state(failure) or RunState.NEEDS_INPUT,
                        reason=outcome.reason or f"node {node.node_id} returned {outcome.status}",
                        error_class=failure,
                    ),
                    runs,
                    wires,
                    stopped_at=node.node_id,
                )
        return RunOutcome(
            run_id=run_id,
            state=self.journal.state_of(run_id),
            completed=True,
            nodes=tuple(runs),
            warnings=tuple(warnings),
            assets=dict(sorted(wires.items())),
        )

    def _preflight(self, run_id: str, root: Mapping[str, str]) -> Optional[_Stop]:
        manifest = self.catalog.get_run(run_id)
        if self.catalog.scope != manifest.project_id:
            return _Stop(
                state=RunState.POLICY_BLOCKED,
                reason=(
                    f"the catalog is scoped to {self.catalog.scope} but run {run_id} belongs to "
                    f"{manifest.project_id}: assets are only read inside their own project's scope"
                ),
                error_class=ErrorClass.POLICY_BLOCKED,
            )
        plan = self.journal.plan_of(run_id)
        missing = sorted(set(plan.root_inputs) - set(root))
        if missing:
            return _Stop(
                state=RunState.NEEDS_INPUT,
                reason=(
                    f"root inputs {missing} name no asset; the plan says what it consumes and the "
                    "caller says which bytes answer it"
                ),
                error_class=ErrorClass.NEEDS_INPUT,
            )
        snapshot = self.registry.snapshot_id()
        if snapshot != manifest.tool_registry_snapshot:
            return _Stop(
                state=RunState.POLICY_BLOCKED,
                reason=(
                    f"the registry now holds {snapshot[:12]} while this run pinned "
                    f"{manifest.tool_registry_snapshot[:12]}: a run executes with the tools its plan "
                    "approval covered, and the registry has changed underneath it"
                ),
                error_class=ErrorClass.POLICY_BLOCKED,
            )
        return None

    # ------------------------------------------------------------------ one node

    def _execute_node(
        self, run_id: str, manifest: RunManifest, node: PlanNode, wires: dict[str, str]
    ) -> Union[NodeRun, _Stop]:
        if node.requires_approval:
            gated = self._gate_refusal(run_id, node)
            if gated is not None:
                return gated

        admitted = self._admit(run_id, node)
        if isinstance(admitted, _Stop):
            return admitted
        spec: ToolSpec = admitted

        wired = self._wiring_refusal(node, spec, wires)
        if wired is not None:
            return wired

        inputs = {slot: wires[slot] for slot in node.inputs}
        try:
            tool_inputs = tuple(
                self._read(slot, asset_id) for slot, asset_id in sorted(inputs.items())
            )
        except IntegrityError as error:
            return self._stop_for(
                ErrorClass.NEEDS_INPUT, f"node {node.node_id} cannot read its input: {error}"
            )

        digests = {item.asset.asset_id: item.asset.file_sha256 for item in tool_inputs}
        input_digest = sha256_of_json(digests)
        parameters_sha = parameters_sha256(node.parameters)

        resources = self._resource_refusal(node, spec)
        if resources is not None:
            return resources

        reused = self._reuse_checkpoint(run_id, manifest, node, spec, input_digest, parameters_sha)
        if reused is not None:
            return reused

        spent = self._spend_adhoc_grant(run_id, node, manifest)
        if spent is not None:
            return spent

        self._record_lineage(
            run_id, node.node_id, {item.slot: item.asset.asset_id for item in tool_inputs}, AssetRole.INPUT
        )

        attempts = self._attempt_limit(node, spec)
        result: Optional[ToolResult] = None
        outcome: Optional[ExecutionOutcome] = None
        minutes = 0.0
        sink: Optional[_NodeSink] = None
        for attempt in range(1, attempts + 1):
            if attempt > 1 and result is not None:
                self.journal.register_node_retry(
                    run_id,
                    node.node_id,
                    attempt=attempt,
                    actor=self.actor,
                    reason=f"attempt {attempt} of {spec.tool_name} after {result.status}: {self._why(result)}",
                )
            #: §3.3: this attempt may have answered already. The receipt is asked before the work is
            #: dispatched, so a delivery that arrives twice is answered from the index instead of
            #: being run a second time.
            receipt = self._receipt_of(
                run_id, node, spec, attempt, input_digest, parameters_sha, manifest.tool_registry_snapshot
            )
            if receipt is not None:
                reused = self._reuse_receipt(run_id, node, spec, receipt)
                if isinstance(reused, _Stop):
                    return reused
                return reused
            started_at = self.clock()
            sink = _NodeSink(
                catalog=self.catalog,
                objects=self.objects,
                run_id=run_id,
                project_id=manifest.project_id,
                node_id=node.node_id,
                defaults=self.asset_defaults,
                created_at=started_at,
            )
            request = ExecutionRequest(
                run_id=run_id,
                node_id=node.node_id,
                attempt=attempt,
                project_id=manifest.project_id,
                spec=spec,
                parameters=dict(node.parameters),
                parameters_sha256=parameters_sha,
                inputs=[
                    ExecutionInput(
                        slot=item.slot,
                        asset_id=item.asset.asset_id,
                        sha256=item.asset.file_sha256,
                        schema_version=item.asset.schema_version,
                        uri=item.asset.uri,
                        content=item.payload,
                    )
                    for item in tool_inputs
                ],
                cage=cage_for(node, spec),
            )
            before = time.monotonic()
            outcome = self.backend.execute(request)
            minutes = max(0.0, self.usage_minutes(node, outcome.result, time.monotonic() - before))
            result = outcome.result
            absorbed = self._absorb(outcome, sink)
            if absorbed is not None:
                return absorbed
            if minutes > 0:
                self.journal.report_usage(
                    run_id,
                    node.node_id,
                    compute_minutes=round(minutes, 6),
                    model_cost_usd=0.0,
                    attempt=attempt,
                    actor=self.actor,
                    reason=f"{spec.tool_name} attempt {attempt} returned {result.status}",
                )
            if result.status is ToolResultStatus.SUCCESS:
                break
            if not self._retryable(result):
                break

        assert result is not None and sink is not None and outcome is not None
        if result.status is not ToolResultStatus.SUCCESS:
            return NodeRun(
                node_id=node.node_id,
                task=spec.task,
                attempt=attempt,
                status=result.status,
                result=result,
                compute_minutes=minutes,
                reason=self._why(result),
            )

        sink.commit()
        output_digests = {asset_id: digests_of(self.catalog, asset_id) for asset_id in sink.written.values()}
        #: §3.3: the answer is now in the index, so the receipt that says so is written before the
        #: checkpoint that resumes from it. A crash between the two leaves the receipt to answer for
        #: the attempt, and a crash before both leaves the assets to be produced again.
        try:
            self.catalog.record_execution_receipt(
                ExecutionReceipt(
                    run_id=run_id,
                    node_id=node.node_id,
                    attempt=attempt,
                    tool_name=spec.tool_name,
                    tool_version=spec.tool_version,
                    input_digest=input_digest,
                    parameters_sha256=parameters_sha,
                    registry_snapshot=manifest.tool_registry_snapshot,
                    outcome_digest=outcome_digest(outcome),
                    status=str(result.status),
                    output_asset_ids=list(sink.written.values()),
                    output_digests=output_digests,
                    executed_on=outcome.executed_on,
                    runtime=outcome.runtime,
                    compute_minutes=round(minutes, 6),
                    at=self.clock(),
                )
            )
        except ExecutionConflictError as error:
            return self._stop_for(ErrorClass.CONFLICT, str(error))
        self.catalog.record_checkpoint(
            RunCheckpoint(
                run_id=run_id,
                node_id=node.node_id,
                attempt=attempt,
                tool_name=spec.tool_name,
                tool_version=spec.tool_version,
                input_digest=input_digest,
                parameters_sha256=parameters_sha,
                registry_snapshot=manifest.tool_registry_snapshot,
                output_asset_ids=list(sink.written.values()),
                output_digests=output_digests,
                compute_minutes=round(minutes, 6),
                at=self.clock(),
            )
        )
        return NodeRun(
            node_id=node.node_id,
            task=spec.task,
            attempt=attempt,
            status=result.status,
            result=result,
            outputs=dict(sorted(sink.written.items())),
            compute_minutes=minutes,
        )

    # ------------------------------------------------------------------ the gates

    def _gate_refusal(self, run_id: str, node: PlanNode) -> Optional[_Stop]:
        """§9.6: the driver reads the approval the journal holds for this node, as it now stands.

        A grant that is simply absent or still pending sends the run to ``NEEDS_INPUT``, because that is
        what it waits for: a person's decision, after which §9.2 lets the same run go back to work. A
        grant that was *refused* is a policy block - the run does not get to try that node again until
        the plan changes.
        """
        approval = self.journal.granted_for(run_id, ApprovalKind.NODE, node_id=node.node_id)
        if approval is not None and approval.is_granted():
            return None
        if approval is None:
            return self._stop_for(
                ErrorClass.NEEDS_INPUT,
                f"node {node.node_id} is gated and no approval covers it as the plan now stands; the "
                f"approval that would answer it is {node_approval_id(run_id, node)} - a node edited "
                "after its approval was granted is not covered by that grant",
            )
        if approval.state is ApprovalState.REJECTED:
            return self._stop_for(
                ErrorClass.POLICY_BLOCKED,
                f"node {node.node_id} was refused by {approval.decided_by}: {approval.decision_reason}",
            )
        return self._stop_for(
            ErrorClass.NEEDS_INPUT,
            f"node {node.node_id} waits on approval {approval.approval_id}, which is {approval.state} "
            f"and assigned to {approval.assigned_to}",
        )

    def _admit(self, run_id: str, node: PlanNode) -> Union[ToolSpec, _Stop]:
        """Resolve the node's tool, and refuse one the registry says may not run at all.

        An ad-hoc grant is not tested here. Its spend happens in ``_spend_adhoc_grant``, after the
        checkpoint has been consulted: a node whose output is already stored was produced by that one
        approval, and a grant spent by the run that stored it cannot answer a second time - so asking
        the question before the checkpoint would leave a resumed run unable to get past its first node
        even though it intends to recompute nothing (§4.1, §9.6).

        An ad-hoc tool the registry does not hold is a wait, not a wedge. The registry holds such a
        tool only while a live grant covers it, so before the grant is issued the node names a tool
        that is not there - and the plain reading of that is a run that can only fail. Nothing has
        gone wrong: a person has yet to issue the grant, and the run goes on once they do (§9.6).
        """
        try:
            return self.registry.resolve(node.tool_name, node.tool_version, now=self.clock())
        except ForbiddenToolError as error:
            return self._stop_for(ErrorClass.POLICY_BLOCKED, f"node {node.node_id} may not run: {error}")
        except UnknownToolError as error:
            if node.tool_class is ToolClass.PROJECT_ADHOC:
                return self._adhoc_grant_wait(run_id, node)
            return self._stop_for(
                ErrorClass.GATE_BLOCKED,
                f"node {node.node_id} names a tool the registry does not hold: {error}; preflight has "
                "to re-run once it is registered",
            )

    def _adhoc_grant_wait(self, run_id: str, node: PlanNode) -> _Stop:
        """Say what an ad-hoc step without an executable grant is waiting for (§9.6).

        The ledger keys a grant by run and node, so it answers exactly one question: has a person
        issued the execution this step needs. No row means the decision is still pending, and the run
        waits in a branch it may be sent back from. A row that is no longer live means the execution
        it bought is spent or its window has closed, which asks for a new decision, not a retry.
        """
        recorded = self.catalog.find_tool_grant(
            node.tool_name, node.tool_version, run_id=run_id, node_id=node.node_id
        )
        if recorded is None:
            return self._stop_for(
                ErrorClass.NEEDS_INPUT,
                f"node {node.node_id} runs {node.tool_name}@{node.tool_version}, an ad-hoc step the "
                f"ledger holds no grant row for {run_id}/{node.node_id}; the step waits on the execution "
                "grant a person issues over the ad-hoc gate (§9.6)",
            )
        if not recorded.is_live(self.clock()):
            return self._stop_for(
                ErrorClass.NEEDS_INPUT,
                f"node {node.node_id} waits on the execution grant {recorded.grant_id}, which is no longer "
                "live: the execution it bought is spent or its window has closed, so the step cannot run "
                "until a person grants it again (§9.6)",
            )
        return self._stop_for(
            ErrorClass.NEEDS_INPUT,
            f"node {node.node_id} names {node.tool_name}@{node.tool_version}, which the registry this "
            f"process hydrated does not hold, though the ledger holds the live grant {recorded.grant_id} "
            "for it; the deployment has to hydrate the registry from the ledger before the step may run "
            "(§9.6)",
        )

    def _spend_adhoc_grant(self, run_id: str, node: PlanNode, manifest: RunManifest) -> Optional[_Stop]:
        """Claim the one execution an ad-hoc node's grant is worth, and stop when it is not there to claim.

        Two ledgers answer, in order. The registry says a permission was admitted into this process at all
        and is still inside its window; the catalog says the row the grant route wrote exists for this very
        run and node, and is still unspent (§9.6). Both have to agree, and the row is the one that outlives
        the process: a permission a registry holds without a row behind it is the out-of-band channel, and
        it buys nothing here. That is what makes "deployments hydrate the registry from the ledger" a rule
        the engine enforces rather than a rule the README asks for.
        A spent grant stays spent, so a second launch or a second run is refused by the row rather than by
        a counter that died with the process that held it.
        """
        if node.tool_class is not ToolClass.PROJECT_ADHOC:
            return None
        try:
            self.registry.check_adhoc(
                node.tool_name, node.tool_version, now=self.clock(), project_id=manifest.project_id
            )
        except (AdhocExhaustedError, ForbiddenToolError, UnknownToolError) as error:
            return self._stop_for(
                ErrorClass.POLICY_BLOCKED,
                f"node {node.node_id} may not execute: {error}; a one-time approval buys one execution "
                "and cannot be re-spent by the engine",
            )
        recorded = self.catalog.find_tool_grant(
            node.tool_name, node.tool_version, run_id=run_id, node_id=node.node_id
        )
        if recorded is None:
            return self._stop_for(
                ErrorClass.POLICY_BLOCKED,
                f"node {node.node_id} may not execute: this process holds a permission for "
                f"{node.tool_name}@{node.tool_version} but the ledger has no grant row for "
                f"{run_id}/{node.node_id}, and only a row written over a person's decision is an execution",
            )
        if self.catalog.spend_tool_grant(recorded.grant_id, at=self.clock()) is None:
            return self._stop_for(
                ErrorClass.POLICY_BLOCKED,
                f"node {node.node_id} may not execute: ad-hoc grant {recorded.grant_id} holds no unspent "
                "execution in the ledger, and the ledger is the record that outlives this process",
            )
        self.registry.consume(
            node.tool_name, node.tool_version, now=self.clock(), project_id=manifest.project_id
        )
        return None

    def _wiring_refusal(self, node: PlanNode, spec: ToolSpec, wires: Mapping[str, str]) -> Optional[_Stop]:
        if spec.task not in TASK_RUNNERS:
            return self._stop_for(
                ErrorClass.NEEDS_INPUT,
                f"node {node.node_id} names {spec.tool_name} {spec.tool_version}, whose registered task "
                f"{spec.task!r} has no implementation in this workflow",
            )
        unwired = sorted(set(required_slots(spec.task)) - set(node.inputs))
        if unwired:
            return self._stop_for(
                ErrorClass.NEEDS_INPUT,
                f"node {node.node_id} does not wire {spec.tool_name}'s required input slots {unwired}; "
                f"the tool reads {list(TASK_INPUT_SLOTS[spec.task])}",
            )
        dangling = sorted(slot for slot in node.inputs if slot not in wires)
        if dangling:
            return self._stop_for(
                ErrorClass.NEEDS_INPUT,
                f"node {node.node_id} consumes {dangling}, which no root input or completed node has "
                "put on the run's wires",
            )
        undeclared = sorted(slot for slot in TASK_RUNNERS[spec.task][1] if slot not in node.outputs)
        if undeclared:
            return self._stop_for(
                ErrorClass.NEEDS_INPUT,
                f"node {node.node_id} runs {spec.tool_name}, whose output slots {undeclared} are not in "
                "its declared outputs, so nothing downstream could ask for them",
            )
        extra = sorted(set(node.outputs) - set(TASK_RUNNERS[spec.task][1]))
        if extra:
            return self._stop_for(
                ErrorClass.NEEDS_INPUT,
                f"node {node.node_id} declares outputs {extra} that {spec.tool_name} does not emit",
            )
        return None

    def _resource_refusal(self, node: PlanNode, spec: ToolSpec) -> Optional[_Stop]:
        """The plan's request has to cover the tool's own measured facts, or the step dies mid-run."""
        measured = spec.resources.measured_peak_memory_mb
        requested = node.resources.memory_gb * 1024
        if measured and requested < measured:
            return self._stop_for(
                ErrorClass.RESOURCE_EXCEEDED,
                f"node {node.node_id} budgets {requested}MB while {spec.tool_name} measured a "
                f"{measured}MB peak on {spec.resources.measured_on}",
            )
        if node.resources.max_minutes < spec.resources.max_minutes:
            return self._stop_for(
                ErrorClass.RESOURCE_EXCEEDED,
                f"node {node.node_id} budgets {node.resources.max_minutes}min while {spec.tool_name} "
                f"declares it may need {spec.resources.max_minutes}min",
            )
        return None

    # ------------------------------------------------------------------ inputs, reuse, retries

    def _read(self, slot: str, asset_id: str) -> _Input:
        asset = self.catalog.get_asset(asset_id)
        if not asset.uri.startswith(URI_SCHEME):
            raise IntegrityError(f"asset {asset_id} is not stored under a platform URI: {asset.uri}")
        if asset.format != ASSET_FORMAT:
            raise IntegrityError(
                f"asset {asset_id} is {asset.format!r}; this workflow reads its own product assets, so a "
                "converted file has to be admitted as an asset before a node can consume it (section 4.1)"
            )
        payload = self.objects.get(asset.uri[len(URI_SCHEME) :])
        if sha256_hex(payload) != asset.file_sha256:
            raise IntegrityError(
                f"asset {asset_id} does not hash to the digest the index holds; bytes and record "
                "disagree, so neither can be trusted as this run's input"
            )
        document = json.loads(payload)
        if document.get("slot") != slot:
            raise IntegrityError(f"asset {asset_id} answers slot {document.get('slot')!r}, not {slot!r}")
        return _Input(
            slot=slot, asset=asset, payload=payload, product=read_product(asset.schema_version, payload)
        )

    def _reuse_checkpoint(
        self,
        run_id: str,
        manifest: RunManifest,
        node: PlanNode,
        spec: ToolSpec,
        input_digest: str,
        parameters_sha: str,
    ) -> Optional[Union[NodeRun, _Stop]]:
        """A matching checkpoint is reused, and reusing it spends nothing (§4.1, §9.2)."""
        checkpoint = self.catalog.matching_checkpoint(
            run_id,
            node.node_id,
            tool_name=spec.tool_name,
            tool_version=spec.tool_version,
            input_digest=input_digest,
            parameters_sha256=parameters_sha,
            registry_snapshot=manifest.tool_registry_snapshot,
        )
        if checkpoint is None:
            return None

        outputs = self._read_recorded_outputs(
            checkpoint.output_asset_ids,
            checkpoint.output_digests,
            source=f"checkpoint {node.node_id}@{checkpoint.attempt}",
        )
        if isinstance(outputs, _Stop):
            return outputs
        self._record_lineage(run_id, node.node_id, outputs, AssetRole.OUTPUT)
        return NodeRun(
            node_id=node.node_id,
            task=spec.task,
            attempt=checkpoint.attempt,
            status=ToolResultStatus.SUCCESS,
            outputs=dict(sorted(outputs.items())),
            reused_checkpoint=True,
            compute_minutes=checkpoint.compute_minutes,
            reason=(
                f"reused checkpoint {node.node_id}@{checkpoint.attempt}: the inputs, parameters and "
                "registry snapshot are the ones it was made from, so nothing was recomputed or charged"
            ),
        )

    def _receipt_of(
        self,
        run_id: str,
        node: PlanNode,
        spec: ToolSpec,
        attempt: int,
        input_digest: str,
        parameters_sha: str,
        registry_snapshot: str,
    ) -> Optional[ExecutionReceipt]:
        """What this attempt answered, if it has answered (§3.3)."""
        return self.catalog.execution_receipt(
            run_id,
            node.node_id,
            attempt,
            tool_name=spec.tool_name,
            tool_version=spec.tool_version,
            input_digest=input_digest,
            parameters_sha256=parameters_sha,
            registry_snapshot=registry_snapshot,
        )

    def _reuse_receipt(
        self, run_id: str, node: PlanNode, spec: ToolSpec, receipt: ExecutionReceipt
    ) -> Union[NodeRun, _Stop]:
        """§3.3: the attempt has answered already, so the work is not run a second time.

        This is the fallback for the window between absorbing an answer and writing the checkpoint
        that resumes from it: the products are in the index, the checkpoint is not, and the answer
        must not be produced again to fill the gap. The checkpoint is written here, so the run
        converges on the shape every other finished node has.
        """
        outputs = self._read_recorded_outputs(
            receipt.output_asset_ids,
            receipt.output_digests,
            source=f"receipt {node.node_id}@{receipt.attempt}",
        )
        if isinstance(outputs, _Stop):
            return outputs
        self._record_lineage(run_id, node.node_id, outputs, AssetRole.OUTPUT)
        self.catalog.record_checkpoint(
            RunCheckpoint(
                run_id=run_id,
                node_id=node.node_id,
                attempt=receipt.attempt,
                tool_name=receipt.tool_name,
                tool_version=receipt.tool_version,
                input_digest=receipt.input_digest,
                parameters_sha256=receipt.parameters_sha256,
                registry_snapshot=receipt.registry_snapshot,
                output_asset_ids=list(receipt.output_asset_ids),
                output_digests=dict(receipt.output_digests),
                compute_minutes=round(receipt.compute_minutes, 6),
                at=self.clock(),
            )
        )
        return NodeRun(
            node_id=node.node_id,
            task=spec.task,
            attempt=receipt.attempt,
            status=ToolResultStatus.SUCCESS,
            outputs=dict(sorted(outputs.items())),
            reused_receipt=True,
            compute_minutes=receipt.compute_minutes,
            reason=(
                f"reused receipt {node.node_id}@{receipt.attempt}: the attempt already answered and its "
                "products are in the index, so nothing was run again (section 3.3)"
            ),
        )

    def _read_recorded_outputs(
        self, asset_ids: Sequence[str], digests: Mapping[str, str], *, source: str
    ) -> Union[dict[str, str], _Stop]:
        """The assets a record names, checked against the index before they are believed.

        A record is a promise about bytes; the index and the object store are what hold the bytes.
        When the two disagree the record is stale, and a stale record may not stand in for work
        that has to be done again.
        """
        outputs: dict[str, str] = {}
        for asset_id in asset_ids:
            asset = self.catalog.get_asset(asset_id)
            if asset.file_sha256 != digests.get(asset_id):
                return self._stop_for(
                    ErrorClass.NEEDS_INPUT,
                    f"{source} points at {asset_id}, whose recorded digest is not the one the index "
                    "holds; the step has to be recomputed",
                )
            payload = self.objects.get(asset.uri[len(URI_SCHEME) :])
            if sha256_hex(payload) != asset.file_sha256:
                return self._stop_for(
                    ErrorClass.NEEDS_INPUT,
                    f"{source} points at {asset_id}, whose bytes no longer match the digest it "
                    "recorded; the step has to be recomputed",
                )
            outputs[str(json.loads(payload)["slot"])] = asset_id
        return outputs

    def _attempt_limit(self, node: PlanNode, spec: ToolSpec) -> int:
        """The tighter of the plan's retry policy and the tool's own retry promise (§9.4).

        A tool that does not declare its retries safe is not retried because a budget happens to allow
        it: ``retry_limit`` is the registry's record of that promise, and ad-hoc tools carry zero.
        """
        return max(1, min(node.retry_policy.max_attempts, spec.retry_limit + 1))

    @staticmethod
    def _retryable(result: ToolResult) -> bool:
        return any(error.error_class is ErrorClass.RETRYABLE_FAILURE for error in result.errors)

    @staticmethod
    def _why(result: ToolResult) -> str:
        if not result.errors:
            return f"{result.tool_name} returned {result.status} without classifying the failure"
        first = result.errors[0]
        return f"{result.tool_name} {result.tool_version}: [{first.error_class}] {first.code} - {first.message}"

    # ------------------------------------------------------------------ outcome plumbing

    # ------------------------------------------------------------------ outcome plumbing

    def _absorb(self, outcome: ExecutionOutcome, sink: _NodeSink) -> Optional[_Stop]:
        """The bytes that came back become assets here, in the control plane that owns the index.

        The node that ran the work has no catalog to write to and the control plane has no step to
        run, so this is where the two meet: each product is read back, checked against the bytes that
        arrived, and written as the asset the run will point at. A product that does not read back as
        the bytes that came back is refused rather than stored, because an asset whose digest
        describes something other than what ran is not a record of anything (§5.6).
        """
        if outcome.status is not outcome.result.status:
            return self._stop_for(
                ErrorClass.NEEDS_INPUT,
                f"the work that came back for {outcome.node_id} says {outcome.status} while the result it "
                f"carries says {outcome.result.status}: the answer and its result disagree, so neither can "
                "be trusted as what ran",
            )
        for item in outcome.outputs:
            try:
                product = read_product(item.schema_version, item.content)
            except (KeyError, ValueError) as error:
                return self._stop_for(
                    ErrorClass.NEEDS_INPUT,
                    f"the work that came back for {outcome.node_id} holds something in slot {item.slot!r} "
                    f"that this platform cannot read: {error}",
                )
            if product.json_bytes() != item.content:
                return self._stop_for(
                    ErrorClass.NEEDS_INPUT,
                    f"the product in slot {item.slot!r} does not read back as the bytes that came back, so "
                    "storing it would store something other than what ran (section 5.6)",
                )
            sink.write(product)
        return None

    @staticmethod
    def _stop_for(error_class: ErrorClass, reason: str) -> _Stop:
        return _Stop(state=to_run_state(error_class) or RunState.NEEDS_INPUT, reason=reason, error_class=error_class)

    def _record_lineage(
        self, run_id: str, node_id: str, slots: Mapping[str, str], role: AssetRole
    ) -> None:
        for ordinal, (_slot, asset_id) in enumerate(sorted(slots.items())):
            self.catalog.record_lineage(
                AssetLineage(run_id=run_id, node_id=node_id, asset_id=asset_id, role=role, ordinal=ordinal)
            )

    def _finish(
        self,
        run_id: str,
        entry: RunState,
        stop: _Stop,
        runs: list[NodeRun],
        wires: Mapping[str, str],
        stopped_at: Optional[str] = None,
    ) -> RunOutcome:
        """Move the run to the branch the refusal names, unless only a person may take it (§9.1)."""
        current = self.journal.state_of(run_id)
        state = current
        needs_human = stop.state is RunState.FAILED
        if needs_human:
            reason = (
                f"{stop.reason} - the run stays in {current} because ending it is a human act; a "
                "service account reporting a stop condition cannot mark its own run FAILED"
            )
        elif stop.state is not current:
            #: A run that waits is executed again and may wait at the same place for the same reason.
            #: Transitions are deduplicated by attempt, so the second wait has to say it is the second
            #: one, or the journal answers it as a redelivery of the first and the run never parks
            #: where the engine says it did (§9.2).
            attempt = self.journal.waits_before(run_id, current, stop.state) + 1
            self.journal.transition(
                run_id, stop.state, actor=self.actor, reason=stop.reason, attempt=attempt
            )
            state = stop.state
            reason = stop.reason
        else:
            reason = stop.reason
        return RunOutcome(
            run_id=run_id,
            state=state,
            completed=False,
            nodes=tuple(runs),
            stopped_at=stopped_at,
            stopped_reason=reason,
            requires_human_action=needs_human,
            assets=dict(sorted(wires.items())),
        )


def digests_of(catalog: Catalog, asset_id: str) -> str:
    """The digest the index holds for a committed asset, so a checkpoint names bytes, not names."""
    return catalog.get_asset(asset_id).file_sha256
