"""The closed loop: one plan executed against the real index, registry, object store and state machine.

Block 5 proved the state machine and this block's unit tests prove the steps. Neither proves the seam
between them, which is where the plan's hardest rules bite: a gated node that runs without a grant
(§9.6), a checkpoint reused for inputs it was not made from (§4.1), an ad-hoc approval spent twice, a
failed attempt indexed as somebody's output, a run whose branch the engine walks out of by itself
(§9.1). Each is asserted here against the real orchestrator and the real catalog rather than against
a fake of them, because a fake only proves the driver agrees with itself.
"""

from __future__ import annotations

from typing import Any, Optional

import pytest
from pydantic import ValidationError

from bioadapt_schemas.enums import (
    ApprovalKind,
    PerturbationKind,
    RetentionClass,
    RunEventKind,
    RunState,
    ToolClass,
)
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_schemas.orchestration import node_approval_id
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS
from bioadapt_schemas.plan import LoopBudget, RetryPolicy
from bioadapt_scrnaseq import SLOT_COUNTS, TASK_QC, ExpressionProduct, read_product, write_asset
from bioadapt_scrnaseq import runner as driver
from bioadapt_scrnaseq.errors import transient
from bioadapt_tool_registry.enums import ToolResultStatus
from bioadapt_tool_registry.registry import ToolRegistry

import driver_factories as F

#: Every slot the reference chain puts on the run's wires, the root input it starts from included.
ALL_SLOTS = {
    SLOT_COUNTS,
    "qc_flags",
    "expression",
    "features",
    "embedding",
    "graph",
    "clusters",
    "markers",
    "de_table",
}

EPHEMERAL_SLOTS = frozenset({"embedding", "graph"})

ADHOC_TOOL, ADHOC_VERSION = "project_qc_probe", "0.1.0"


class Flaky:
    """Fails the first ``fails`` executions of one node, at the wrapper seam.

    A production step fails for reasons the reference steps cannot produce here - a node that vanishes,
    a container killed on its memory limit - so the classification is injected there. Everything else
    stays real: the step still runs and still writes its bytes, and the driver still has to decide
    whether to retry, what to charge, and what not to index.
    """

    def __init__(self, monkeypatch: Any, *, node_id: str, fails: int, error: Any = None) -> None:
        real = driver.execute
        error = error or transient("the compute node dropped the container before the step reported")
        self.attempts: list[str] = []

        def fake(invocation: Any, sink: Any, **kwargs: Any) -> Any:
            result = real(invocation, sink, **kwargs)
            self.attempts.append(invocation.node_id)
            tried = sum(1 for node_id in self.attempts if node_id == invocation.node_id)
            if invocation.node_id != node_id or tried > fails:
                return result
            return result.model_copy(
                update={"status": ToolResultStatus.FAILED, "output_asset_ids": [], "errors": [error.as_api_error()]}
            )

        monkeypatch.setattr(driver, "execute", fake)


def registry_with_qc_retry_limit(limit: int) -> ToolRegistry:
    """The reference tools, with one step's own retry promise changed (§9.4)."""
    registry = ToolRegistry()
    for spec in F.reference_tool_specs(F.DEPLOYMENT).values():
        updated = spec.model_copy(update={"retry_limit": limit})
        registry.register(updated if spec.task == TASK_QC else spec)
    return registry


def adhoc_registry() -> ToolRegistry:
    """The reference tools plus one ad-hoc QC submission carrying its single-use grant (§9.6)."""
    registry = F.reference_registry()
    registry.grant(F.adhoc_qc_spec(), F.adhoc_grant())
    return registry


def adhoc_nodes() -> list[Any]:
    """The same chain, with its QC node run through the ad-hoc channel instead of the registered tool."""
    return F.chain_nodes(
        qc={
            "tool_name": ADHOC_TOOL,
            "tool_version": ADHOC_VERSION,
            "tool_class": ToolClass.PROJECT_ADHOC,
            "requires_approval": True,
        }
    )


def adhoc_harness(harness_factory: Any) -> F.Harness:
    """The ad-hoc chain, with its gated QC node answered by a person (§9.6).

    An ad-hoc submission carries two separate permissions: the registry grant, which is one-time, and
    this run's approval of the node as the plan states it. The harness sets both up so a test can break
    one without tripping over the other.
    """
    made = harness_factory(registry=adhoc_registry(), the_plan=F.plan(nodes=adhoc_nodes()))
    made.approve_node("qc")
    return made


def staged_asset(h: F.Harness, product: ExpressionProduct) -> str:
    """Index a product as one of this project's assets, the way an upload is staged."""
    return write_asset(
        h.catalog,
        h.objects,
        product,
        project_id=F.PROJECT_ID,
        defaults=F.ASSET_DEFAULTS,
        created_at=h.clock(),
    )


def output_rows(h: F.Harness, assets: dict[str, str], *, node_id: Optional[str] = None) -> list[Any]:
    rows = [row for asset_id in assets.values() for row in h.catalog.lineage(asset_id)]
    return [row for row in rows if row.role.value == "output" and (node_id is None or row.node_id == node_id)]


@pytest.fixture
def completed(harness_factory: Any) -> tuple:
    """The reference plan run once to completion, which most tests then break in exactly one place."""
    made = harness_factory()
    outcome = made.run()
    assert outcome.completed, outcome.stopped_reason
    return made, outcome


# ------------------------------------------------------------------ what a completed run leaves behind


def test_the_plan_runs_end_to_end_and_every_slot_is_answered(completed: tuple) -> None:
    h, out = completed
    assert [node.node_id for node in out.nodes] == [node_id for node_id, _task in F.CHAIN]
    assert set(out.assets) == ALL_SLOTS
    for slot, asset_id in out.assets.items():
        assert h.asset(asset_id).project_id == F.PROJECT_ID, slot
    assert all(node.attempt == 1 and node.status is ToolResultStatus.SUCCESS for node in out.nodes)


def test_the_project_labels_retention_and_only_the_run_made_its_assets(completed: tuple) -> None:
    h, out = completed
    #: Section 5.9: which intermediates a rerun may recompute is the project's call, not the step's.
    for slot in EPHEMERAL_SLOTS:
        assert h.asset(out.assets[slot]).retention_class is RetentionClass.EPHEMERAL, slot
    for slot in ALL_SLOTS - set(EPHEMERAL_SLOTS):
        assert h.asset(out.assets[slot]).retention_class is RetentionClass.RUN_LINKED, slot
    #: The root input was staged before the run began, so the run cannot claim it as something it made.
    produced = {asset.asset_id for asset in h.catalog.list_assets(run_id=F.RUN_ID)}
    assert h.root[SLOT_COUNTS] not in produced
    assert produced <= set(out.assets.values())


def test_lineage_records_which_node_answered_which_slot(completed: tuple) -> None:
    h, out = completed
    assert [(row.role.value, row.node_id) for row in h.catalog.lineage(out.assets["de_table"])] == [
        ("output", "pseudobulk_de")
    ]
    staged = h.catalog.lineage(h.root[SLOT_COUNTS])
    assert {row.node_id for row in staged if row.role.value == "input"} >= {"root", "qc"}
    assert len(output_rows(h, out.assets)) == len(ALL_SLOTS), "every produced slot has exactly one publisher"


def test_a_lossy_conversion_is_declared_and_both_derived_assets_survive(completed: tuple) -> None:
    """Section 4.1: a lossy transform keeps what it came from and says what it dropped."""
    h, out = completed
    lossy = [warning for warning in out.warnings if "lossy" in warning]
    assert lossy and any("counts->log1p" in warning for warning in lossy)
    assert "raw_counts" in lossy[0]
    counts, expression = h.asset(out.assets[SLOT_COUNTS]), h.asset(out.assets["expression"])
    assert counts.file_sha256 != expression.file_sha256, "the log product replaced the counts it came from"
    assert counts.content_fingerprint != expression.content_fingerprint


def test_the_run_charges_one_usage_event_per_charged_attempt(completed: tuple) -> None:
    h, _out = completed
    events = h.events(kind=str(RunEventKind.USAGE))
    assert len(events) == len(F.CHAIN)
    assert {event.node_id for event in events} == {node_id for node_id, _task in F.CHAIN}
    assert all(event.actor_id == F.SERVICE and event.attempt == 1 for event in events)
    assert h.usage().compute_minutes == pytest.approx(F.MINUTES_PER_ATTEMPT * len(F.CHAIN))


def test_two_independent_runs_of_one_plan_agree_byte_for_byte(harness_factory: Any) -> None:
    """Section 10.1 L1: same plan, same inputs, same tools, and the digests match twice over."""
    first = harness_factory().run()
    second = harness_factory().run()
    assert first.assets == second.assets
    assert [node.result.execution.parameters_sha256 for node in first.nodes] == [
        node.result.execution.parameters_sha256 for node in second.nodes
    ]


def test_a_checkpointed_rerun_recomputes_nothing_and_charges_nothing(harness_factory: Any) -> None:
    h = harness_factory()
    first = h.run()
    usage, events, keys = h.usage(), len(h.events(kind=str(RunEventKind.USAGE))), len(h.objects.keys())
    second = h.run()
    assert second.completed and second.assets == first.assets
    assert all(node.reused_checkpoint for node in second.nodes), [
        node.node_id for node in second.nodes if not node.reused_checkpoint
    ]
    assert h.usage() == usage, "reusing a checkpoint spends nothing (section 9.2)"
    assert len(h.events(kind=str(RunEventKind.USAGE))) == events
    assert len(h.objects.keys()) == keys, "a reused node wrote no bytes a second time"


def test_a_recomputed_node_leaves_a_checkpoint_beside_the_one_it_displaced(harness_factory: Any) -> None:
    """§4.1: a changed parameter is new provenance, not an overwrite of what is still stored."""
    h = harness_factory()
    first = h.run()
    h.reindex(F.plan(plan_version="2", parameters_for={"markers": {"top_n": 3}}))
    second = h.run()
    assert second.completed
    assert [node.node_id for node in second.nodes if not node.reused_checkpoint] == ["markers"]
    assert second.assets["markers"] != first.assets["markers"]
    assert len(h.catalog.list_checkpoints(F.RUN_ID)) == len(F.CHAIN) + 1


# ------------------------------------------------------------------ the gate (§9.6)


def test_a_gated_node_runs_only_when_the_journal_holds_its_grant(harness_factory: Any) -> None:
    h = harness_factory(the_plan=F.plan(gated=("clustering",)))
    stopped = h.run()
    assert stopped.stopped_at == "clustering" and stopped.state is RunState.NEEDS_INPUT
    assert [node.node_id for node in stopped.nodes] == [node_id for node_id, _task in F.CHAIN[:5]]

    #: The refusal names the approval a person can actually grant: the node's own digest, addressed to
    #: this run. A driver that invented its own id would ask for something no journal could ever hold.
    expected = node_approval_id(F.RUN_ID, h.node("clustering"))
    assert expected in stopped.stopped_reason
    assert h.approve_node("clustering") == expected

    h.resume()
    done = h.run()
    assert done.completed
    assert [node.node_id for node in done.nodes[5:]] == ["clustering", "markers", "pseudobulk_de"]
    assert all(node.reused_checkpoint for node in done.nodes[:5]), "a branch resumes, it does not restart"


def test_an_approval_does_not_cover_a_node_that_changed_after_it_was_granted(harness_factory: Any) -> None:
    h = harness_factory(the_plan=F.plan(gated=("clustering",)))
    granted = h.approve_node("clustering")
    h.reindex(F.plan(plan_version="2", gated=("clustering",), parameters_for={"clustering": {"resolution": 3.0}}))

    stopped = h.run()
    assert stopped.stopped_at == "clustering" and stopped.state is RunState.NEEDS_INPUT
    required = node_approval_id(F.RUN_ID, h.node("clustering"))
    assert required != granted and required in stopped.stopped_reason

    h.approve_node("clustering")
    h.resume()
    assert h.run().completed


def test_a_refused_grant_is_a_policy_block_not_a_waiting_state(harness_factory: Any) -> None:
    h = harness_factory(the_plan=F.plan(gated=("clustering",)))
    approval = h.journal.request_approval(
        F.RUN_ID, ApprovalKind.NODE, actor=F.HUMAN_ANALYST, node_id="clustering", assigned_to=F.PI
    )
    h.journal.decide_approval(approval.approval_id, actor=F.HUMAN_PI, approved=False, reason="not this design")
    stopped = h.run()
    assert stopped.state is RunState.POLICY_BLOCKED and stopped.stopped_at == "clustering"
    assert "not this design" in stopped.stopped_reason and F.PI in stopped.stopped_reason


def test_a_pending_grant_waits_for_the_person_it_is_assigned_to(harness_factory: Any) -> None:
    h = harness_factory(the_plan=F.plan(gated=("clustering",)))
    pending = h.journal.request_approval(
        F.RUN_ID, ApprovalKind.NODE, actor=F.HUMAN_ANALYST, node_id="clustering", assigned_to=F.PI
    )
    stopped = h.run()
    assert stopped.state is RunState.NEEDS_INPUT and stopped.stopped_at == "clustering"
    assert pending.approval_id in stopped.stopped_reason and F.PI in stopped.stopped_reason


# ------------------------------------------------------------------ admission (§9.4, §9.6)


def test_a_registry_that_changed_after_the_plan_was_approved_blocks_the_run(harness_factory: Any) -> None:
    h = harness_factory()
    bumped = F.reference_tool_specs(F.DEPLOYMENT, tasks=("qc",))["scrnaseq_qc"].model_copy(
        update={"tool_version": "2.0.0"}
    )
    h.registry.register(bumped)
    stopped = h.run()
    assert stopped.state is RunState.POLICY_BLOCKED
    assert stopped.nodes == () and h.usage().compute_minutes == 0.0
    assert "registry has changed underneath it" in stopped.stopped_reason


def test_a_tool_the_registry_does_not_hold_blocks_and_asks_for_preflight(harness_factory: Any) -> None:
    h = harness_factory(the_plan=F.plan(nodes=F.chain_nodes(qc={"tool_version": "9.9.9"})))
    stopped = h.run()
    assert stopped.stopped_at == "qc" and stopped.state is RunState.BLOCKED
    assert "preflight has to re-run" in stopped.stopped_reason


def test_an_adhoc_grant_buys_exactly_one_execution(harness_factory: Any) -> None:
    h = adhoc_harness(harness_factory)
    done = h.run()
    assert done.completed
    assert done.result_of("qc").unregistered is True, "section 9.6: an ad-hoc result is marked as one"

    #: The grant is spent, so a second run may not execute the same submission even once it is approved.
    second = h.start_run("run_scrnaseq_02")
    h.approve_node("qc", run_id=second)
    stopped = h.run(run_id=second)
    assert stopped.stopped_at == "qc" and stopped.state is RunState.POLICY_BLOCKED
    assert "already used" in stopped.stopped_reason
    assert h.usage(second).compute_minutes == 0.0


def test_a_checkpointed_adhoc_node_does_not_spend_the_grant_twice(harness_factory: Any) -> None:
    """The spend has to come after the checkpoint, or a resumed run could never get past node one."""
    h = adhoc_harness(harness_factory)
    first = h.run()
    assert first.completed and not first.node("qc").reused_checkpoint
    second = h.run()
    assert second.completed and second.node("qc").reused_checkpoint


def test_an_expired_adhoc_grant_blocks_before_the_step_runs(harness_factory: Any) -> None:
    h = adhoc_harness(harness_factory)
    h.clock.advance(days=30)
    stopped = h.run()
    assert stopped.state is RunState.POLICY_BLOCKED and stopped.stopped_at == "qc"
    assert "expired" in stopped.stopped_reason
    assert stopped.nodes == ()


def test_a_memory_budget_below_the_measured_peak_is_refused(harness_factory: Any) -> None:
    h = harness_factory(the_plan=F.plan(nodes=F.chain_nodes(normalize={"memory_gb": 1})))
    stopped = h.run()
    assert stopped.stopped_at == "normalize" and stopped.state is RunState.RESOURCE_EXCEEDED
    assert f"{F.MEASURED_PEAK_MEMORY_MB}MB" in stopped.stopped_reason


def test_a_time_budget_below_what_the_tool_declares_is_refused(harness_factory: Any) -> None:
    h = harness_factory(the_plan=F.plan(nodes=F.chain_nodes(markers={"max_minutes": 10})))
    stopped = h.run()
    assert stopped.stopped_at == "markers" and stopped.state is RunState.RESOURCE_EXCEEDED
    assert "declares it may need" in stopped.stopped_reason


# ------------------------------------------------------------------ the plan's own wiring (§4.1)


def test_a_missing_root_input_is_an_input_gap_not_a_failure(harness_factory: Any) -> None:
    h = harness_factory()
    stopped = h.runner.run(F.RUN_ID, root={})
    assert stopped.state is RunState.NEEDS_INPUT and stopped.stopped_at is None
    assert f"[{SLOT_COUNTS!r}]" in stopped.stopped_reason


def test_a_node_that_does_not_wire_its_required_slot_is_refused(harness_factory: Any) -> None:
    h = harness_factory(the_plan=F.plan(nodes=F.chain_nodes(clustering={"inputs": []})))
    stopped = h.run()
    assert stopped.stopped_at == "clustering" and stopped.state is RunState.NEEDS_INPUT
    assert "required input slots" in stopped.stopped_reason


def test_a_plan_that_wires_a_slot_nothing_produces_is_not_admitted() -> None:
    """The plan contract checks the wiring before a run can be built on it (§4.1)."""
    with pytest.raises(ValidationError, match="phantom"):
        F.plan(nodes=F.chain_nodes(markers={"inputs": ["expression", "phantom"]}))


def test_a_node_that_omits_a_slot_its_tool_emits_is_refused(harness_factory: Any) -> None:
    h = harness_factory(the_plan=F.plan(nodes=F.chain_nodes(qc={"outputs": ["qc_flags"]})))
    stopped = h.run()
    assert stopped.stopped_at == "qc" and SLOT_COUNTS in stopped.stopped_reason
    assert "not in its declared outputs" in stopped.stopped_reason


def test_a_step_handed_the_wrong_kind_of_matrix_is_stopped_by_the_wrapper(harness_factory: Any) -> None:
    """§9.4: a plan may wire a slot, but it cannot make a log-normalised matrix mean counts."""
    first = harness_factory()
    out = first.run()
    stored = first.asset(out.assets["expression"])
    log = read_product(stored.schema_version, first.objects.get(stored.uri[len("s3://") :]))
    assert isinstance(log, ExpressionProduct) and not log.counts

    h = harness_factory()
    h.root[SLOT_COUNTS] = staged_asset(
        h,
        ExpressionProduct(
            slot=SLOT_COUNTS, matrix=log.matrix, transform=log.transform, parameters={"staged_by": "wrong state"}
        ),
    )
    stopped = h.run()
    assert stopped.stopped_at == "qc" and not stopped.completed
    assert "sums or measures raw counts" in stopped.stopped_reason
    assert f"slot {SLOT_COUNTS!r} is {log.transform!r}" in stopped.stopped_reason


# ------------------------------------------------------------------ integrity of what is stored


def test_a_tampered_root_input_is_refused_before_a_node_reads_it(completed: tuple) -> None:
    h, _out = completed
    restaged = h.start_run("run_scrnaseq_02")
    h.rewrite_object(h.root[SLOT_COUNTS], b'{"not": "a count matrix"}')
    stopped = h.run(run_id=restaged)
    assert stopped.stopped_at == "qc" and stopped.state is RunState.NEEDS_INPUT
    assert "cannot read its input" in stopped.stopped_reason
    assert "does not hash to the digest the index holds" in stopped.stopped_reason


def test_a_tampered_checkpoint_output_is_not_resumed_as_if_it_were_intact(completed: tuple) -> None:
    h, out = completed
    h.rewrite_object(out.assets["markers"], b'{"slot": "markers"}')
    stopped = h.run()
    assert stopped.stopped_at == "markers" and stopped.state is RunState.NEEDS_INPUT
    assert "no longer match the digest it recorded" in stopped.stopped_reason
    assert all(node.reused_checkpoint for node in stopped.nodes[:6])


def test_an_asset_answering_a_different_slot_is_not_read_for_it(harness_factory: Any) -> None:
    """Slots are the plan's vocabulary; an asset cannot claim to be one thing and be another (§4.1)."""
    h = harness_factory()
    asset_id = staged_asset(
        h,
        ExpressionProduct(
            slot="expression", matrix=h.data.matrix, transform="log1p", parameters={"staged_by": "wrong slot"}
        ),
    )
    stopped = h.runner.run(F.RUN_ID, root={SLOT_COUNTS: asset_id})
    assert stopped.state is RunState.NEEDS_INPUT
    assert f"answers slot 'expression', not {SLOT_COUNTS!r}" in stopped.stopped_reason


def test_a_converted_file_is_not_read_until_it_is_admitted_as_an_asset(harness_factory: Any) -> None:
    """Section 4.1: a conversion produces a file; admission as an asset is what gives it an identity."""
    h = harness_factory()
    payload = b"HDF5 placeholder for a converted anndata file"
    digest = sha256_hex(payload)
    key = f"{F.PROJECT_ID}/converted/as_{digest[:12]}.h5ad"
    h.objects.put(key, payload)
    staged = h.asset(h.root[SLOT_COUNTS]).model_copy(
        update={
            "asset_id": f"as_{digest[:12]}",
            "uri": f"s3://{key}",
            "file_sha256": digest,
            "format": "h5ad",
            "size_bytes": len(payload),
        }
    )
    h.catalog.record_asset(staged)
    stopped = h.runner.run(F.RUN_ID, root={SLOT_COUNTS: staged.asset_id})
    assert stopped.state is RunState.NEEDS_INPUT
    assert "section 4.1" in stopped.stopped_reason and "h5ad" in stopped.stopped_reason


# ------------------------------------------------------------------ retries (§9.2, §9.4)


def test_a_retryable_failure_is_retried_as_far_as_the_tool_promises(
    harness_factory: Any, monkeypatch: Any
) -> None:
    h = harness_factory(registry=registry_with_qc_retry_limit(2))
    flaky = Flaky(monkeypatch, node_id="qc", fails=2)
    done = h.run()
    assert done.completed and done.node("qc").attempt == 3
    assert flaky.attempts.count("qc") == 3
    assert [event.attempt for event in h.events(kind=str(RunEventKind.NODE_RETRY))] == [2, 3]
    assert [event.attempt for event in h.events(kind=str(RunEventKind.USAGE)) if event.node_id == "qc"] == [1, 2, 3]
    assert h.usage().compute_minutes == pytest.approx(F.MINUTES_PER_ATTEMPT * (len(F.CHAIN) + 2))


def test_a_tool_that_declares_no_retries_is_tried_once(harness_factory: Any, monkeypatch: Any) -> None:
    """Section 9.4: the registry's retry_limit, not a plan's budget, says whether a retry is safe."""
    h = harness_factory()
    flaky = Flaky(monkeypatch, node_id="qc", fails=1)
    stopped = h.run()
    assert stopped.stopped_at == "qc" and stopped.state is RunState.RETRYABLE_FAILURE
    assert flaky.attempts == ["qc"], "one attempt, because scrnaseq_qc never promised a safe retry"
    assert h.events(kind=str(RunEventKind.NODE_RETRY)) == []
    #: The failed attempt wrote its bytes; nothing about it entered the index.
    assert h.catalog.list_assets(run_id=F.RUN_ID) == []
    assert h.catalog.list_checkpoints(F.RUN_ID) == []
    assert len(h.objects.keys()) > 1, "the orphan is storage's to collect, not the run's to publish"


def test_a_retried_attempt_publishes_exactly_what_a_clean_one_would(
    harness_factory: Any, monkeypatch: Any
) -> None:
    h = harness_factory(registry=registry_with_qc_retry_limit(2))
    Flaky(monkeypatch, node_id="qc", fails=1)
    done = h.run()
    assert done.completed and done.node("qc").attempt == 2
    #: One publisher per produced slot, even though the first attempt wrote those same bytes and failed.
    assert len(output_rows(h, done.assets)) == len(ALL_SLOTS)
    assert len({row.asset_id for row in output_rows(h, done.assets, node_id="qc")}) == 2


def test_the_plans_retry_policy_is_the_tighter_ceiling(harness_factory: Any, monkeypatch: Any) -> None:
    h = harness_factory(
        registry=registry_with_qc_retry_limit(5),
        the_plan=F.plan(nodes=F.chain_nodes(qc={"retry": RetryPolicy(max_attempts=2, backoff_seconds=0)})),
    )
    flaky = Flaky(monkeypatch, node_id="qc", fails=99)
    stopped = h.run()
    assert stopped.stopped_at == "qc" and stopped.state is RunState.RETRYABLE_FAILURE
    assert flaky.attempts.count("qc") == 2
    assert len(h.events(kind=str(RunEventKind.NODE_RETRY))) == 1, "the plan sets how often, not the engine"


# ------------------------------------------------------------------ stop conditions (§9.1, §9.2)


def test_a_stop_condition_reports_to_a_person_and_does_not_end_the_run(harness_factory: Any) -> None:
    """Section 9.1: a service account that could set FAILED could end any run it disagreed with."""
    h = harness_factory(the_plan=F.plan(parameters_for={"qc": {"max_flagged_share": 0.0}}))
    stopped = h.run()
    assert stopped.stopped_at == "qc" and not stopped.completed
    assert stopped.state is RunState.ANALYSIS_RUNNING, "the run stays where it was: ending it is a human act"
    assert stopped.requires_human_action is True and "human act" in stopped.stopped_reason
    assert stopped.result_of("qc").status is ToolResultStatus.FAILED
    assert h.catalog.list_assets(run_id=F.RUN_ID) == [] and h.catalog.list_checkpoints(F.RUN_ID) == []


def test_a_spent_loop_ceiling_escalates_in_the_middle_of_the_plan(harness_factory: Any) -> None:
    """§9.2: the ceiling is checked per attempt, so a runaway plan stops now rather than after itself."""
    h = harness_factory(
        the_plan=F.plan(loop_budget=LoopBudget(max_iterations=1, max_compute_minutes=1, max_token_cost_usd=5.0))
    )
    stopped = h.run()
    assert not stopped.completed and stopped.state is RunState.ESCALATED_TO_HUMAN
    assert 0 < len(stopped.nodes) < len(F.CHAIN), stopped.nodes
    assert "left ANALYSIS_RUNNING for ESCALATED_TO_HUMAN" in stopped.stopped_reason
    assert h.usage().compute_minutes == pytest.approx(F.MINUTES_PER_ATTEMPT * len(stopped.nodes))


def test_a_run_parked_on_a_branch_is_not_executed_into(harness_factory: Any) -> None:
    h = harness_factory()
    h.journal.transition(F.RUN_ID, RunState.NEEDS_INPUT, actor=F.ENGINE, reason="waiting for a person")
    stopped = h.run()
    assert stopped.nodes == () and stopped.state is RunState.NEEDS_INPUT and stopped.stopped_at is None
    assert "leaving a branch is the state machine's act" in stopped.stopped_reason
    assert h.usage().compute_minutes == 0.0

    h.resume()
    assert h.run().completed, "the same run goes back to work once the person has answered"


# ---------------------------------------------------------- the candidate follow-ups, as plan nodes


#: Sections 7.5 and 7.6 add three measurements after the chain: a stability protocol, a coverage
#: measurement and a continuous-state check. They are plan nodes like every other one, so the driver owes
#: them the same admission, budgeting, lineage and byte-level reproducibility - and they owe the reviewer
#: products whose verdicts are recomputed from stored numbers.
FOLLOW_UP_SLOTS = ALL_SLOTS | {"stability", "coverage", "state_axis"}


def stored(h: F.Harness, out: Any, slot: str) -> Any:
    """Read one asset back through the contract its index row names, the way a reviewer would."""
    asset = h.asset(out.assets[slot])
    return read_product(asset.schema_version, h.objects.get(asset.uri[len("s3://") :]))


@pytest.fixture(scope="module")
def followed_up() -> Any:
    """One follow-up run, shared. The robustness node re-runs the chain for every arm, so a run costs
    more than the tests around it and none of them changes the plan."""
    made = F.harness(the_plan=F.plan(nodes=F.follow_up_nodes()))
    outcome = made.run()
    assert outcome.completed, outcome.stopped_reason
    yield made, outcome
    made.catalog.close()


def test_the_follow_up_plan_runs_and_answers_every_slot_it_names(followed_up: tuple) -> None:
    h, out = followed_up
    assert [node.node_id for node in out.nodes] == [nid for nid, _task in F.CHAIN] + [
        nid for nid, _task in F.FOLLOW_UP
    ]
    assert set(out.assets) == FOLLOW_UP_SLOTS
    assert len(output_rows(h, out.assets)) == len(FOLLOW_UP_SLOTS), "one publisher per produced slot"
    assert all(node.attempt == 1 and node.status is ToolResultStatus.SUCCESS for node in out.nodes)
    assert h.usage().compute_minutes == pytest.approx(F.MINUTES_PER_ATTEMPT * len(out.nodes))


def test_a_follow_up_node_reads_the_assets_the_chain_published(followed_up: tuple) -> None:
    """The seam §4.1 is about: the plan wired these nodes to real outputs, and the journal says so."""
    h, out = followed_up
    readers = {row.node_id for row in h.catalog.lineage(out.assets["clusters"]) if row.role.value == "input"}
    assert {"robustness", "coverage", "state_axis"} <= readers
    #: A product names the exact version it measured, so a reader can check it against the index.
    coverage = stored(h, out, "coverage")
    axis = stored(h, out, "state_axis")
    stability = stored(h, out, "stability")
    assert coverage.embedding_digest == h.asset(out.assets["embedding"]).file_sha256
    assert coverage.reference_digest == axis.cluster_digest == stability.baseline_clusters_digest


def test_the_follow_up_products_hold_measurements_and_recompute_their_own_verdicts(followed_up: tuple) -> None:
    h, out = followed_up
    stability = stored(h, out, "stability")
    assert stability.covered_kinds == tuple(sorted(kind.value for kind in PerturbationKind.mandatory_protocol()))
    assert stability.omitted_arms == () and stability.deviation is None
    assert stability.thresholds_version == DEFAULT_THRESHOLDS.version
    #: The verdict is a function of the stored agreement, never a stored answer.
    assert stability.stable is (stability.verdict() == "stable")

    coverage = stored(h, out, "coverage")
    assert coverage.criteria == {
        "knn": float(DEFAULT_THRESHOLDS.coverage_knn),
        "distance_ratio_min": float(DEFAULT_THRESHOLDS.coverage_distance_ratio_min),
    }
    assert coverage.coverage_gap is (
        coverage.distance_ratio >= coverage.criteria["distance_ratio_min"]
    ), "a gap is derived, so a driver cannot write one"

    axis = stored(h, out, "state_axis")
    assert axis.axis == {"CD14": 1.0, "CD3D": -1.0}
    assert axis.is_axis_tail is (
        axis.tail_share >= axis.criteria["tail_share_min"]
        and axis.axis_explained >= axis.criteria["axis_explained_min"]
    )


def test_a_follow_up_asset_rereads_to_the_contract_and_digests_it_was_indexed_under(followed_up: tuple) -> None:
    """§10.1: the bytes in storage re-read to the same numbers, so a reviewer needs no compute to check."""
    h, out = followed_up
    for slot in ("stability", "coverage", "state_axis"):
        asset = h.asset(out.assets[slot])
        product = stored(h, out, slot)
        assert product.file_sha256() == asset.file_sha256, slot
        assert product.content_fingerprint() == asset.content_fingerprint, slot
