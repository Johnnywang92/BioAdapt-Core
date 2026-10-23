"""The in-process backend: the same work, behind the seam the driver now uses (§3.3).

The driver asks a backend to do the work rather than calling the reference step itself, so what lives
here is the promise that the work did not change: the bytes that come back are the bytes the step
wrote, the answer is addressed to the attempt that asked for it, and an input whose bytes do not
match the digest the request names is refused rather than read - a matrix that lost bytes on the way
here would otherwise be measured as a matrix with holes in it.
"""

from __future__ import annotations

from datetime import date

import pytest

from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_schemas.thresholds import DEFAULT_THRESHOLDS
from bioadapt_tool_registry.execution import ExecutionCage, ExecutionInput, ExecutionRequest

from bioadapt_scrnaseq.backends import InProcessBackend, asset_id_for_bytes
from bioadapt_scrnaseq.implementation import parameters_sha256
from bioadapt_scrnaseq.products import read_product
from bioadapt_scrnaseq.specs import TASK_QC, Deployment, reference_tool_specs
from bioadapt_scrnaseq.steps import SLOT_COUNTS
from dataset import MITO_GENES, RIBOSOMAL_GENES, synthetic_dataset
from workflow_factories import qc_policy, qc_step_for

RUN_ID, PROJECT_ID = "run_unit", "proj_unit"

DEPLOYMENT = Deployment(
    container_repository="registry.internal/bioadapt/scrnaseq-reference",
    container_tag="1.0.0",
    container_digest="sha256:" + "ab" * 32,
    tool_version="1.0.0",
    species=("human",),
    cpu=4,
    memory_mb=16384,
    max_minutes=60,
    measured_peak_memory_mb=1200,
    measured_on="synthetic_pbmc_v1",
    license_spdx_id="BSD-3-Clause",
    license_display_name="Scanpy reference steps",
    license_reviewed_at=date(2026, 5, 4),
    license_review_note="reviewed against the platform's own licence matrix on 2026-05-04",
)
QC_SPEC = next(spec for spec in reference_tool_specs(DEPLOYMENT).values() if spec.task == TASK_QC)

#: The stage-0 numbers the QC node runs with. They are inputs, so the request carries them.
QC_PARAMETERS = {
    "qc_policy": qc_policy(),
    "thresholds_version": DEFAULT_THRESHOLDS.version,
    "max_flagged_share": 0.35,
    "mito_genes": list(MITO_GENES),
    "ribosomal_genes": list(RIBOSOMAL_GENES),
}


def request_for(product, **overrides) -> ExecutionRequest:
    """A request as the control plane would hand it over: the bytes, not a reference to them."""
    payload = product.json_bytes()
    fields = {
        "run_id": RUN_ID,
        "node_id": "qc",
        "attempt": 1,
        "project_id": PROJECT_ID,
        "spec": QC_SPEC,
        "parameters": dict(QC_PARAMETERS),
        "parameters_sha256": parameters_sha256(QC_PARAMETERS),
        "inputs": [
            ExecutionInput(
                slot=SLOT_COUNTS,
                asset_id=asset_id_for_bytes(payload),
                sha256=sha256_hex(payload),
                schema_version=product.schema_version,
                uri="s3://proj_unit/run_unit/as_counts.json",
                content=payload,
            )
        ],
        "cage": ExecutionCage(cpu=4, memory_mb=16384, max_minutes=60),
    }
    fields.update(overrides)
    return ExecutionRequest(**fields)


@pytest.fixture(scope="module")
def counts():
    """The counts a QC node is handed, produced by the step rather than written by hand."""
    return qc_step_for(synthetic_dataset()).product(SLOT_COUNTS)


def test_the_answer_is_addressed_to_the_attempt_that_asked_for_it(counts) -> None:
    """§3.3: a callback is correlated by run, node and attempt, so the answer has to say which."""
    outcome = InProcessBackend().execute(request_for(counts))
    assert outcome.key == (RUN_ID, "qc", 1)
    assert outcome.result.run_id == RUN_ID and outcome.result.node_id == "qc"
    assert outcome.status is outcome.result.status


def test_the_products_come_back_as_the_bytes_the_step_wrote(counts) -> None:
    """The node's bytes are the node's bytes: what comes back is what the platform stores (§5.6)."""
    outcome = InProcessBackend().execute(request_for(counts))
    assert {item.slot for item in outcome.outputs} == {"qc_flags", "counts"}
    for item in outcome.outputs:
        assert item.sha256 == sha256_hex(item.content)
        restored = read_product(item.schema_version, item.content)
        assert restored.slot == item.slot
        assert restored.file_sha256() == item.sha256


def test_the_asset_id_is_the_same_whoever_computed_it(counts) -> None:
    """§5.2: the id names the bytes, so the node and the control plane cannot disagree about it."""
    outcome = InProcessBackend().execute(request_for(counts))
    assert set(outcome.result.output_asset_ids) == {
        asset_id_for_bytes(item.content) for item in outcome.outputs
    }


def test_an_input_whose_bytes_do_not_match_the_digest_is_refused(counts) -> None:
    """A request that lost bytes would otherwise be read as a matrix with holes in it."""
    request = request_for(counts)
    lying = request.inputs[0].model_copy(update={"sha256": "cd" * 32})
    with pytest.raises(ValueError, match="does not hash to the digest the request named"):
        InProcessBackend().execute(request.model_copy(update={"inputs": [lying]}))


def test_the_cage_says_what_the_attempt_may_not_have(counts) -> None:
    """A limit the control plane only remembers is a limit nobody enforces where the work happens."""
    cage = ExecutionCage(cpu=2, memory_mb=512, max_minutes=30)
    request = request_for(counts, cage=cage)
    assert request.cage is cage
    # The memory line names the measured peak (1200MB), not the 16GB the tool may burst to: that is
    # the figure the plan's own budget floor is built from, so the two must agree.
    assert cage.holds(QC_SPEC) == [
        "the tool wants 4 cpu, the cage allows 2",
        "the tool needs 1200MB of memory, the cage allows 512MB",
        "the tool wants 60min, the cage allows 30min",
    ]
