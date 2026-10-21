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
from typing import Any, Callable, Mapping, Optional, Protocol, Union

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
from bioadapt_schemas.orchestration import Approval, RunCheckpoint, node_approval_id
from bioadapt_schemas.plan import AnalysisPlan, PlanNode
from bioadapt_storage.catalog import Catalog
from bioadapt_storage.objectstore import ObjectStore, object_key
from bioadapt_tool_registry.enums import ToolResultStatus
from bioadapt_tool_registry.registry import (
    AdhocExhaustedError,
    ForbiddenToolError,
    ToolRegistry,
    UnknownToolError,
)
from bioadapt_tool_registry.result import ToolResult
from bioadapt_tool_registry.spec import ToolSpec

from .implementation import (
    TASK_INPUT_SLOTS,
    TASK_RUNNERS,
    Invocation,
    ToolInput,
    execute,
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

        admitted = self._admit(node)
        if isinstance(admitted, _Stop):
            return admitted
        spec: ToolSpec = admitted

        wired = self._wiring_refusal(node, spec, wires)
        if wired is not None:
            return wired

        inputs = {slot: wires[slot] for slot in node.inputs}
        try:
            tool_inputs = tuple(
                ToolInput(slot=slot, asset_id=asset_id, product=self._read(slot, asset_id))
                for slot, asset_id in sorted(inputs.items())
            )
        except IntegrityError as error:
            return self._stop_for(
                ErrorClass.NEEDS_INPUT, f"node {node.node_id} cannot read its input: {error}"
            )

        digests = {
            asset_id: self.catalog.get_asset(asset_id).file_sha256 for asset_id in sorted({item.asset_id for item in tool_inputs})
        }
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

        self._record_lineage(run_id, node.node_id, {item.slot: item.asset_id for item in tool_inputs}, AssetRole.INPUT)

        attempts = self._attempt_limit(node, spec)
        result: Optional[ToolResult] = None
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
            invocation = Invocation(
                run_id=run_id,
                node_id=node.node_id,
                project_id=manifest.project_id,
                spec=spec,
                inputs=tool_inputs,
                parameters=dict(node.parameters),
            )
            before = time.monotonic()
            result = execute(invocation, sink, started_at=started_at, finished_at=self.clock())
            minutes = max(0.0, self.usage_minutes(node, result, time.monotonic() - before))
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

        assert result is not None and sink is not None
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

    def _admit(self, node: PlanNode) -> Union[ToolSpec, _Stop]:
        """Resolve the node's tool, and refuse one the registry says may not run at all.

        An ad-hoc grant is not tested here. Its spend happens in ``_spend_adhoc_grant``, after the
        checkpoint has been consulted: a node whose output is already stored was produced by that one
        approval, and a grant spent by the run that stored it cannot answer a second time - so asking
        the question before the checkpoint would leave a resumed run unable to get past its first node
        even though it intends to recompute nothing (§4.1, §9.6).
        """
        try:
            return self.registry.resolve(node.tool_name, node.tool_version, now=self.clock())
        except ForbiddenToolError as error:
            return self._stop_for(ErrorClass.POLICY_BLOCKED, f"node {node.node_id} may not run: {error}")
        except UnknownToolError as error:
            return self._stop_for(
                ErrorClass.GATE_BLOCKED,
                f"node {node.node_id} names a tool the registry does not hold: {error}; preflight has "
                "to re-run once it is registered",
            )

    def _spend_adhoc_grant(self, run_id: str, node: PlanNode, manifest: RunManifest) -> Optional[_Stop]:
        """Claim the one execution an ad-hoc node's grant is worth, and stop when it is not there to claim.

        Two ledgers answer, in order. The registry says a permission was admitted into this process at all
        and is still inside its window; the catalog says whether the row the grant route wrote is still
        unspent (§9.6). Only when both agree is the in-process copy consumed, so a second launch - or a
        second run - is refused by the row rather than by a counter that died with the process that held it.
        A registry grant with no row is the out-of-band channel: it can only ever be spent once by the
        process that holds it, which is why a deployment builds its registry from the catalog.
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
        if recorded is not None and self.catalog.spend_tool_grant(recorded.grant_id, at=self.clock()) is None:
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

    def _read(self, slot: str, asset_id: str) -> Product:
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
        return read_product(asset.schema_version, payload)

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

        outputs: dict[str, str] = {}
        for asset_id in checkpoint.output_asset_ids:
            asset = self.catalog.get_asset(asset_id)
            if asset.file_sha256 != checkpoint.output_digests.get(asset_id):
                return self._stop_for(
                    ErrorClass.NEEDS_INPUT,
                    f"checkpoint {node.node_id}@{checkpoint.attempt} points at {asset_id}, whose recorded "
                    "digest is not the one the index holds; the step has to be recomputed",
                )
            payload = self.objects.get(asset.uri[len(URI_SCHEME) :])
            if sha256_hex(payload) != asset.file_sha256:
                return self._stop_for(
                    ErrorClass.NEEDS_INPUT,
                    f"checkpoint {node.node_id}@{checkpoint.attempt} points at {asset_id}, whose bytes no "
                    "longer match the digest it recorded; the step has to be recomputed",
                )
            outputs[str(json.loads(payload)["slot"])] = asset_id
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
            self.journal.transition(run_id, stop.state, actor=self.actor, reason=stop.reason)
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
