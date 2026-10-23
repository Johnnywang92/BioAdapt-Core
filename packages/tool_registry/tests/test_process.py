"""The node process backend: the work runs in a child process, inside the cage (§3.3, §4.2, §9.6).

A node is a machine that is not the control plane, and the promises that matter there are the ones
nobody in the control plane can keep on the node's behalf: the attempt really did run in its own
process, it really did stop at the ceiling the request named, and a node that cannot keep a limit
says so instead of running the work anyway. The cases below are proved against a real child
process, because a fake of one would only prove the backend agrees with itself.
"""

from __future__ import annotations

import fcntl
import json
import os
import sys
import time
from pathlib import Path
from typing import Any

import pytest

from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_tool_registry import (
    PROTOCOL_VERSION,
    ExecutionCage,
    ExecutionInput,
    ExecutionRequest,
    ProcessBackend,
    read_process_request,
    write_process_request,
)
from bioadapt_tool_registry.enums import ToolResultStatus
from bioadapt_tool_registry.process import LOCK_FILE, OUTCOME_FILE, REQUEST_FILE
from bioadapt_tool_registry.spec import ResourceProfile

from registry_factories import D64, adhoc_spec, complete_spec

PAYLOAD = b'{"slot":"counts","schema_version":"counts_v1"}'

#: A node stands in for the tool: it reads the request the parent staged, and answers with the
#: products it made. The other modes are the ways an attempt goes wrong on a node.
FAKE_NODE = '''
import json
import os
import sys
import time
from pathlib import Path

from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_tool_registry.enums import ToolResultStatus
from bioadapt_tool_registry.execution import ExecutionOutcome, ExecutionOutput
from bioadapt_tool_registry.process import read_process_request, write_process_outcome
from bioadapt_tool_registry.result import ToolExecution, ToolResult

mode = os.environ.get("FAKE_NODE_MODE", "answer")
work_dir = os.environ["BIOADAPT_WORK_DIR"]
request = read_process_request(work_dir)

count = os.environ.get("FAKE_NODE_COUNT")
if count:
    with open(count, "a", encoding="utf-8") as handle:
        handle.write("ran\\n")

report = os.environ.get("FAKE_NODE_REPORT")
if report:
    import resource
    Path(report).write_text(json.dumps({"cpu": list(resource.getrlimit(resource.RLIMIT_CPU))}))

env_report = os.environ.get("FAKE_NODE_ENV_REPORT")
if env_report:
    Path(env_report).write_text(json.dumps(dict(os.environ)))

if mode == "sleep":
    time.sleep(300)
elif mode == "grow":
    ballast = bytearray(400 * 1024 * 1024)
    ballast[::4096] = b"\\x01" * len(range(0, len(ballast), 4096))
    time.sleep(300)
elif mode == "die":
    print("the node lost the container before the step reported", file=sys.stderr)
    sys.exit(9)
else:
    payload = b'{"slot":"counts"}'
    result = ToolResult(
        run_id=request.run_id,
        node_id=request.node_id,
        project_id=request.project_id,
        tool_name=request.spec.tool_name,
        tool_version=request.spec.tool_version,
        status=ToolResultStatus.SUCCESS,
        execution=ToolExecution(
            container_image_digest=request.spec.container_image.digest,
            tool_version=request.spec.tool_version,
            parameters_sha256=request.parameters_sha256,
        ),
        input_asset_ids=[item.asset_id for item in request.inputs],
        input_hashes={item.asset_id: item.sha256 for item in request.inputs},
        output_asset_ids=["as_out"],
    )
    outcome = ExecutionOutcome(
        run_id=request.run_id,
        node_id=request.node_id,
        attempt=request.attempt + (1 if mode == "other_attempt" else 0),
        status=ToolResultStatus.SUCCESS,
        result=result,
        outputs=[ExecutionOutput(slot="counts", schema_version="counts_v1",
                                 sha256=sha256_hex(payload), content=payload)],
        runtime=os.environ.get("FAKE_NODE_RUNTIME") or None,
    )
    write_process_outcome(work_dir, outcome)
'''


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
                sha256=sha256_hex(PAYLOAD),
                schema_version="counts_v1",
                uri="s3://bioadapt/prj_001/counts.json",
                content=PAYLOAD,
            )
        ],
        "cage": cage(),
    }
    fields.update(overrides)
    return ExecutionRequest(**fields)


def node(tmp_path: Path, **kwargs: Any) -> ProcessBackend:
    """A backend whose child is the stand-in node above.

    What the child does is chosen by the environment it inherits, so the test says which way the
    node will misbehave before the attempt starts.
    """
    script = tmp_path / "fake_node.py"
    script.write_text(FAKE_NODE, encoding="utf-8")
    kwargs.setdefault("command", [sys.executable, str(script)])
    kwargs.setdefault("work_root", tmp_path / "work")
    kwargs.setdefault("node_name", "node_laptop")
    kwargs.setdefault(
        "env_passthrough",
        (
            "FAKE_NODE_MODE",
            "FAKE_NODE_COUNT",
            "FAKE_NODE_REPORT",
            "FAKE_NODE_RUNTIME",
            "FAKE_NODE_ENV_REPORT",
        ),
    )
    return ProcessBackend(**kwargs)


class TestRunning:
    def test_the_work_runs_in_its_own_process_and_the_products_come_back_as_bytes(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "answer")
        backend = node(tmp_path)
        outcome = backend.execute(request())

        assert outcome.status is ToolResultStatus.SUCCESS
        assert [item.slot for item in outcome.outputs] == ["counts"]
        assert outcome.outputs[0].content == b'{"slot":"counts"}'
        assert outcome.executed_on == "node_laptop", "the answer says which machine ran it (§3.3)"
        assert outcome.key == ("run_prj_001_01", "qc_001", 1)

    def test_the_inputs_are_staged_beside_the_request_and_checked_against_their_digests(
        self, tmp_path: Path
    ) -> None:
        work_dir = tmp_path / "staged"
        work_dir.mkdir()
        write_process_request(work_dir, request())

        staged = sorted((work_dir / "inputs").iterdir())
        assert [item.read_bytes() for item in staged] == [PAYLOAD]

        back = read_process_request(work_dir)
        assert back == request()

        staged[0].write_bytes(b"not the bytes the request named")
        with pytest.raises(ValueError, match="does not hash"):
            read_process_request(work_dir)

    def test_the_answer_of_a_finished_attempt_stays_where_a_redelivery_will_find_it(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "answer")
        backend = node(tmp_path)
        backend.execute(request())
        kept = tmp_path / "work" / "run_prj_001_01" / "qc_001" / "attempt-1"
        assert (kept / OUTCOME_FILE).exists(), "the answer is what a redelivery has to get"
        assert (kept / REQUEST_FILE).exists(), "the request is what tells a redelivery apart"

    def test_the_staged_request_names_the_protocol_it_speaks(self, tmp_path: Path) -> None:
        work_dir = tmp_path / "staged"
        work_dir.mkdir()
        write_process_request(work_dir, request())

        staged = json.loads((work_dir / REQUEST_FILE).read_text(encoding="utf-8"))
        assert staged["protocol"] == PROTOCOL_VERSION

    def test_a_staged_request_from_a_side_that_speaks_another_version_is_refused(
        self, tmp_path: Path
    ) -> None:
        work_dir = tmp_path / "staged"
        work_dir.mkdir()
        path = write_process_request(work_dir, request())
        staged = json.loads(path.read_text(encoding="utf-8"))
        staged["protocol"] = PROTOCOL_VERSION + 1
        path.write_text(json.dumps(staged), encoding="utf-8")

        with pytest.raises(ValueError, match="protocol"):
            read_process_request(work_dir)


class TestRuntime:
    """§9.4: the record names what ran, not the image the tool says it runs in."""

    def test_an_answer_that_does_not_say_what_ran_is_a_host_process(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "answer")
        outcome = node(tmp_path).execute(request())

        assert outcome.runtime is not None
        assert outcome.runtime.startswith("host-python ")

    def test_an_answer_that_names_its_own_runtime_is_believed(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "answer")
        monkeypatch.setenv("FAKE_NODE_RUNTIME", "bioadapt-scrnaseq 0.1.0 on python 3.12.11")
        outcome = node(tmp_path).execute(request())

        assert outcome.runtime == "bioadapt-scrnaseq 0.1.0 on python 3.12.11"


class TestWhatTheChildInherits:
    """§9.6: the node's own secrets stay on the node, not in the code it runs."""

    def test_the_node_credential_does_not_reach_the_child(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "answer")
        monkeypatch.setenv("BIOADAPT_NODE_TOKEN", "the deployment's secret")
        seen = tmp_path / "environment.json"
        monkeypatch.setenv("FAKE_NODE_ENV_REPORT", str(seen))

        outcome = node(tmp_path).execute(request())

        assert outcome.status is ToolResultStatus.SUCCESS
        environment = json.loads(seen.read_text(encoding="utf-8"))
        assert "BIOADAPT_NODE_TOKEN" not in environment
        assert environment["BIOADAPT_WORK_DIR"] == str(
            tmp_path / "work" / "run_prj_001_01" / "qc_001" / "attempt-1"
        )


class TestRedelivery:
    """§3.3: a lost answer is a round trip, not a second run."""

    def runs(self, tmp_path: Path, monkeypatch: Any) -> Path:
        counter = tmp_path / "runs.txt"
        monkeypatch.setenv("FAKE_NODE_COUNT", str(counter))
        return counter

    def test_a_redelivered_attempt_is_answered_from_the_record(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "answer")
        counter = self.runs(tmp_path, monkeypatch)
        backend = node(tmp_path)

        first = backend.execute(request())
        again = backend.execute(request())

        assert again.status is ToolResultStatus.SUCCESS
        assert [item.sha256 for item in again.outputs] == [item.sha256 for item in first.outputs]
        assert again.compute_minutes == first.compute_minutes
        assert again.runtime == first.runtime
        assert counter.read_text(encoding="utf-8").count("ran") == 1, "the work ran once"

    def test_a_failed_attempt_is_answered_again_rather_than_rerun(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "die")
        counter = self.runs(tmp_path, monkeypatch)
        backend = node(tmp_path)

        first = backend.execute(request())
        again = backend.execute(request())

        assert first.status is ToolResultStatus.FAILED
        assert again.errors[0].code == "attempt_died"
        assert counter.read_text(encoding="utf-8").count("ran") == 1

    def test_different_work_under_the_same_attempt_is_a_conflict(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "answer")
        counter = self.runs(tmp_path, monkeypatch)
        backend = node(tmp_path)
        assert backend.execute(request()).status is ToolResultStatus.SUCCESS

        second = backend.execute(
            request(parameters={"min_genes": 100}, parameters_sha256=sha256_hex(b"other work"))
        )

        assert second.status is ToolResultStatus.FAILED
        assert second.errors[0].code == "attempt_conflict"
        assert second.errors[0].error_class is ErrorClass.CONFLICT
        assert counter.read_text(encoding="utf-8").count("ran") == 1

    def test_an_attempt_that_is_still_running_is_not_started_again(self, tmp_path: Path) -> None:
        work_dir = tmp_path / "work" / "run_prj_001_01" / "qc_001" / "attempt-1"
        work_dir.mkdir(parents=True)
        holding = (work_dir / LOCK_FILE).open("a+")
        fcntl.flock(holding.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        try:
            outcome = node(tmp_path).execute(request())
        finally:
            holding.close()

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "attempt_in_flight"
        assert outcome.errors[0].error_class is ErrorClass.RETRYABLE_FAILURE

    def test_an_attempt_kept_past_its_window_is_removed(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        stale = tmp_path / "work" / "run_old" / "qc_old" / "attempt-1"
        stale.mkdir(parents=True)
        (stale / "leftover.txt").write_text("scratch", encoding="utf-8")
        two_hours_ago = time.time() - 2 * 3600
        os.utime(stale, (two_hours_ago, two_hours_ago))
        monkeypatch.setenv("FAKE_NODE_MODE", "answer")

        node(tmp_path, retain_hours=1).execute(request())

        assert not stale.exists()


class TestTheCage:
    def test_a_tool_the_cage_does_not_hold_does_not_start(self, tmp_path: Path) -> None:
        backend = node(tmp_path)
        outcome = backend.execute(request(cage=cage(cpu=1)))

        assert outcome.status is ToolResultStatus.POLICY_BLOCKED
        assert "cpu" in outcome.errors[0].message
        assert not (tmp_path / "work").exists(), "nothing ran, so there is nothing to look at"

    def test_an_adhoc_attempt_does_not_run_where_the_network_cannot_be_closed(
        self, tmp_path: Path
    ) -> None:
        backend = node(tmp_path)
        outcome = backend.execute(request(spec=adhoc_spec()))

        assert outcome.status is ToolResultStatus.POLICY_BLOCKED
        assert "ad-hoc" in outcome.errors[0].message

    def test_the_cpu_ceiling_is_held_by_the_child_itself(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "answer")
        report = tmp_path / "limits.json"
        monkeypatch.setenv("FAKE_NODE_REPORT", str(report))
        brief = ResourceProfile(
            cpu=1, memory_mb=256, max_minutes=4, measured_peak_memory_mb=128, measured_on="run_prj_001_01"
        )
        backend = node(tmp_path)
        outcome = backend.execute(
            request(spec=complete_spec(resources=brief), cage=cage(cpu=1, memory_mb=256, max_minutes=4))
        )

        assert outcome.status is ToolResultStatus.SUCCESS
        seen = json.loads(report.read_text(encoding="utf-8"))
        assert seen["cpu"][0] == 4 * 60, "the kernel holds the child to the ceiling, not the parent"

    def test_a_multi_core_budget_is_the_whole_time_the_plan_approved(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        """Four cores for four minutes is sixteen core-minutes, not four minutes of one core."""
        monkeypatch.setenv("FAKE_NODE_MODE", "answer")
        report = tmp_path / "limits.json"
        monkeypatch.setenv("FAKE_NODE_REPORT", str(report))
        brief = ResourceProfile(
            cpu=4,
            memory_mb=16384,
            max_minutes=4,
            measured_peak_memory_mb=12100,
            measured_on="run_prj_001_01",
        )
        backend = node(tmp_path)
        outcome = backend.execute(
            request(
                spec=complete_spec(resources=brief),
                cage=cage(cpu=4, memory_mb=16384, max_minutes=4),
            )
        )

        assert outcome.status is ToolResultStatus.SUCCESS
        seen = json.loads(report.read_text(encoding="utf-8"))
        assert seen["cpu"][0] == 4 * 60 * 4

    def test_an_attempt_that_outlasts_its_ceiling_is_stopped(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "sleep")
        monkeypatch.setattr("bioadapt_tool_registry.process.SECONDS_PER_MINUTE", 0.02)
        backend = node(tmp_path)
        outcome = backend.execute(request(cage=cage(max_minutes=60)))

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "attempt_too_long"
        assert outcome.errors[0].error_class is ErrorClass.RESOURCE_EXCEEDED

    def test_an_attempt_that_outgrows_its_memory_ceiling_is_stopped(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "grow")
        small = ResourceProfile(
            cpu=1, memory_mb=256, max_minutes=60, measured_peak_memory_mb=128, measured_on="run_prj_001_01"
        )
        backend = node(tmp_path)
        outcome = backend.execute(
            request(spec=complete_spec(resources=small), cage=cage(cpu=1, memory_mb=256))
        )

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "attempt_too_large"
        assert outcome.errors[0].error_class is ErrorClass.RESOURCE_EXCEEDED


class TestFailures:
    def test_an_attempt_that_dies_is_reported_with_what_it_said(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "die")
        backend = node(tmp_path)
        outcome = backend.execute(request())

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "attempt_died"
        assert "the node lost the container" in outcome.errors[0].message

    def test_an_answer_about_other_work_is_refused(
        self, tmp_path: Path, monkeypatch: Any
    ) -> None:
        monkeypatch.setenv("FAKE_NODE_MODE", "other_attempt")
        backend = node(tmp_path)
        outcome = backend.execute(request())

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "answer_mismatch"
