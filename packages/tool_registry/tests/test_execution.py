"""The execution seam (plan V2.0 sections 3.3, 9.4, 9.6, 9.7).

The seam is what makes "the work runs over there" possible, so the cases below hold one line: a
request says what the work is and what it may have, an outcome comes back addressed to the exact
attempt that produced it, and nothing that crosses the boundary is taken on trust.
"""

from __future__ import annotations

import pytest

from bioadapt_schemas.enums import ToolClass
from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_tool_registry import (
    PROTOCOL_VERSION,
    ExecutionCage,
    ExecutionInput,
    ExecutionOutcome,
    ExecutionOutput,
    ExecutionRequest,
    execution_key,
    outcome_digest,
    outcome_from_envelope,
    outcome_to_envelope,
    request_from_envelope,
    request_to_envelope,
)
from bioadapt_tool_registry.enums import ToolResultStatus

from registry_factories import D64, NOW, adhoc_spec, complete_spec, result

PAYLOAD = b'{"slot":"counts","schema_version":"counts_v1"}'
PAYLOAD_SHA = sha256_hex(PAYLOAD)


def cage(**overrides) -> ExecutionCage:
    fields = {"cpu": 8, "memory_mb": 32768, "max_minutes": 240}
    fields.update(overrides)
    return ExecutionCage(**fields)


def request(**overrides) -> ExecutionRequest:
    fields = {
        "run_id": "run_prj_001_01",
        "node_id": "qc_001",
        "attempt": 1,
        "project_id": "prj_001",
        "spec": complete_spec(),
        "parameters": {"min_genes": 200},
        "parameters_sha256": D64,
        "inputs": [
            ExecutionInput(
                slot="counts",
                asset_id="as_0001",
                sha256=PAYLOAD_SHA,
                schema_version="counts_v1",
                uri="s3://bioadapt/prj_001/counts.json",
                content=PAYLOAD,
            )
        ],
        "cage": cage(),
    }
    fields.update(overrides)
    return ExecutionRequest(**fields)


def outcome(**overrides) -> ExecutionOutcome:
    fields = {
        "run_id": "run_prj_001_01",
        "node_id": "qc_001",
        "attempt": 1,
        "status": ToolResultStatus.SUCCESS,
        "result": result(),
        "outputs": [
            ExecutionOutput(
                slot="counts", schema_version="counts_v1", sha256=PAYLOAD_SHA, content=PAYLOAD
            )
        ],
        "compute_minutes": 1.5,
        "started_at": NOW,
        "finished_at": NOW,
        "executed_on": "nod_server_01",
        "runtime": "bioadapt-scrnaseq 0.1.0 on python 3.12.11",
    }
    fields.update(overrides)
    return ExecutionOutcome(**fields)


class TestCage:
    def test_a_tool_that_fits_the_cage_is_held_by_it(self) -> None:
        assert cage().holds(complete_spec()) == []

    def test_a_tool_that_wants_more_cpu_than_the_cage_allows_says_so(self) -> None:
        reasons = cage(cpu=2).holds(complete_spec())
        assert any("cpu" in reason for reason in reasons)

    def test_a_tool_that_wants_more_memory_than_the_cage_allows_says_so(self) -> None:
        reasons = cage(memory_mb=4096).holds(complete_spec())
        assert any("memory" in reason for reason in reasons)

    def test_a_tool_that_runs_longer_than_the_cage_allows_says_so(self) -> None:
        reasons = cage(max_minutes=10).holds(complete_spec())
        assert any("min" in reason for reason in reasons)

    def test_a_gpu_is_refused_unless_the_cage_allows_one(self) -> None:
        assert any("GPU" in reason for reason in cage().holds(complete_spec(resources={"cpu": 4, "memory_mb": 16384, "max_minutes": 60, "gpu": 1})))
        assert cage(allow_gpu=True).holds(
            complete_spec(resources={"cpu": 4, "memory_mb": 16384, "max_minutes": 60, "gpu": 1})
        ) == []


class TestAddressing:
    def test_the_key_is_the_run_the_node_and_the_attempt(self) -> None:
        assert request().key == ("run_prj_001_01", "qc_001", 1)
        assert execution_key("run_prj_001_01", "qc_001", 1) == "run_prj_001_01:qc_001:1"

    def test_a_second_attempt_is_a_different_key(self) -> None:
        assert request(attempt=2).key != request(attempt=1).key

    def test_an_outcome_that_names_other_work_is_refused(self) -> None:
        with pytest.raises(ValueError, match="name different work"):
            outcome(run_id="run_prj_002_01")


class TestEnvelope:
    def test_a_request_survives_the_boundary(self) -> None:
        back = request_from_envelope(request_to_envelope(request()))
        assert back == request()

    def test_an_outcome_survives_the_boundary(self) -> None:
        back = outcome_from_envelope(outcome_to_envelope(outcome()))
        assert back == outcome()

    def test_bytes_that_do_not_match_the_digest_are_refused(self) -> None:
        envelope = request_to_envelope(request())
        envelope["inputs"][0]["content_b64"] = "bm90IHRoZSBieXRlcyB0aGUgZW52ZWxvcGUgbmFtZWQ="
        with pytest.raises(ValueError, match="does not hash"):
            request_from_envelope(envelope)

    def test_an_output_that_does_not_match_the_digest_is_refused(self) -> None:
        envelope = outcome_to_envelope(outcome())
        envelope["outputs"][0]["sha256"] = "ab" * 32
        with pytest.raises(ValueError, match="does not hash"):
            outcome_from_envelope(envelope)

    def test_both_envelopes_name_the_protocol_they_speak(self) -> None:
        assert request_to_envelope(request())["protocol"] == PROTOCOL_VERSION
        assert outcome_to_envelope(outcome())["protocol"] == PROTOCOL_VERSION

    def test_a_request_from_a_side_that_speaks_another_version_is_refused(self) -> None:
        envelope = request_to_envelope(request())
        envelope["protocol"] = PROTOCOL_VERSION + 1
        with pytest.raises(ValueError, match="protocol"):
            request_from_envelope(envelope)

    def test_an_outcome_from_a_side_that_names_no_version_is_refused(self) -> None:
        envelope = outcome_to_envelope(outcome())
        del envelope["protocol"]
        with pytest.raises(ValueError, match="does not name a protocol version"):
            outcome_from_envelope(envelope)


class TestOutcomeDigest:
    def test_the_same_answer_twice_has_the_same_digest(self) -> None:
        assert outcome_digest(outcome()) == outcome_digest(outcome())

    def test_a_different_answer_has_a_different_digest(self) -> None:
        other = outcome(outputs=[ExecutionOutput(slot="counts", schema_version="counts_v1", sha256="ab" * 32, content=b"other")])
        assert outcome_digest(other) != outcome_digest(outcome())

    def test_a_different_status_has_a_different_digest(self) -> None:
        failed = outcome(
            status=ToolResultStatus.FAILED,
            result=result(
                status=ToolResultStatus.FAILED,
                output_asset_ids=[],
                errors=[{"code": "x", "error_class": ErrorClass.FAILED, "message": "no"}],
            ),
        )
        assert outcome_digest(failed) != outcome_digest(outcome())


def test_an_adhoc_tool_is_carried_by_the_request_it_is_given() -> None:
    assert request(spec=adhoc_spec()).spec.tool_class is ToolClass.PROJECT_ADHOC
