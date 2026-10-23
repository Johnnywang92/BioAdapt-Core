"""The node transport: a control plane hands work to another machine over HTTP (§3.3, §4.2).

The transport is proved against a real socket and a real agent, because the things that go wrong
between two machines do not go wrong between two functions: the credential is checked at the door,
a request that does not read as a request is refused rather than run, and a node that cannot be
reached comes back as a classified failure rather than an exception that takes the engine with it.

The work that runs on the far side is the same work the in-process backend runs, so the bytes that
come back are the bytes the step wrote - which is the whole claim of section 4.2.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Iterator, Optional

import pytest

from bioadapt_schemas.errors import ErrorClass
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_tool_registry import ExecutionOutcome, ExecutionOutput, HttpNodeBackend
from bioadapt_tool_registry.enums import ToolResultStatus
from bioadapt_tool_registry.execution import outcome_to_envelope
from bioadapt_tool_registry.node_agent import ATTEMPT_PATH
from bioadapt_tool_registry.result import ToolExecution, ToolResult

from node_factories import TOKEN, backend_for, cage, make_agent, request, serve, stop
from registry_factories import D64, adhoc_spec


@pytest.fixture
def agent(tmp_path: Path) -> Iterator[Any]:
    """A node listening on a free port, with the stand-in tool behind it."""
    made = make_agent(tmp_path)
    server, thread = serve(made)
    made.server = server
    try:
        yield made
    finally:
        stop(server, thread)


class TestTheJourney:
    def test_the_work_runs_on_the_node_and_the_bytes_come_back(self, agent: Any) -> None:
        outcome = backend_for(agent).execute(request())

        assert outcome.status is ToolResultStatus.SUCCESS
        assert [item.slot for item in outcome.outputs] == ["counts"]
        assert outcome.outputs[0].content == b'{"slot":"counts"}'
        assert outcome.executed_on == "nod_server_01", "the answer says which machine ran it"
        assert outcome.runtime is not None and outcome.runtime.startswith("host-python ")
        assert outcome.key == ("run_prj_001_01", "qc_001", 1)

    def test_the_work_really_happened_on_the_other_side(self, agent: Any, tmp_path: Path) -> None:
        outcome = backend_for(agent).execute(request())
        assert outcome.status is ToolResultStatus.SUCCESS
        #: The node staged the attempt in its own scratch, under the run and node that asked for it.
        assert (tmp_path / "work" / "run_prj_001_01" / "qc_001").is_dir()
        #: The bytes are the ones the far side wrote, not the ones this process's own step would.
        assert outcome.outputs[0].content == b'{"slot":"counts"}'


class TestTheDoor:
    def test_a_call_with_the_wrong_credential_is_refused(self, agent: Any) -> None:
        outcome = backend_for(agent, token="not the secret").execute(request())

        assert outcome.status is ToolResultStatus.POLICY_BLOCKED
        assert outcome.errors[0].code == "node_refused_credentials"
        assert outcome.errors[0].error_class is ErrorClass.POLICY_BLOCKED

    def test_the_node_refuses_a_caller_without_a_credential(self, agent: Any) -> None:
        host, port = agent.server.server_address[0], agent.server.server_address[1]
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                urllib.request.Request(
                    f"http://{host}:{port}{ATTEMPT_PATH}",
                    data=b"{}",
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
            )
        assert caught.value.code == 401

    def test_the_node_refuses_a_body_that_is_not_a_request(self, agent: Any) -> None:
        host, port = agent.server.server_address[0], agent.server.server_address[1]
        with pytest.raises(urllib.error.HTTPError) as caught:
            urllib.request.urlopen(
                urllib.request.Request(
                    f"http://{host}:{port}{ATTEMPT_PATH}",
                    data=b"not json at all",
                    headers={"Authorization": f"Bearer {TOKEN}", "Content-Type": "application/json"},
                    method="POST",
                )
            )
        assert caught.value.code == 400


class TestTheHealthCheck:
    """§3.3, §4.2: ask who is listening before handing it any work."""

    def test_the_declared_machine_answers_with_its_name_and_protocol(self, agent: Any) -> None:
        assert backend_for(agent).probe() is None

    def test_a_listener_that_cannot_name_itself_is_not_the_declared_machine(self) -> None:
        class Anonymous(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - the name is the http.server's
                body = json.dumps({}).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server, thread = _serve(Anonymous)
        try:
            host, port = server.server_address[0], server.server_address[1]
            reason = HttpNodeBackend(
                base_url=f"http://{host}:{port}", token=TOKEN, node_name="node_other"
            ).probe()
        finally:
            stop(server, thread)

        assert reason is not None and "declared the machine as" in reason

    def test_a_health_check_with_the_wrong_credential_is_refused(self, agent: Any) -> None:
        reason = backend_for(agent, token="not the secret").probe()

        assert reason is not None and "credential" in reason


class TestTheFailures:
    def test_a_node_that_cannot_be_reached_is_a_classified_failure(self, agent: Any) -> None:
        agent.server.shutdown()
        agent.server.server_close()
        outcome = backend_for(agent).execute(request())

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "node_unreachable"
        assert outcome.errors[0].error_class is ErrorClass.RETRYABLE_FAILURE

    def test_an_answer_about_other_work_is_refused(self) -> None:
        """§3.3: an answer about another attempt is not an answer about this one."""
        server, thread = _serve_answer(_answer(attempt=2))
        try:
            host, port = server.server_address[0], server.server_address[1]
            outcome = HttpNodeBackend(
                base_url=f"http://{host}:{port}", token=TOKEN, node_name="node_other"
            ).execute(request())
        finally:
            stop(server, thread)

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "answer_mismatch"

    def test_an_answer_that_cannot_say_what_ran_is_refused(self) -> None:
        """§9.4: products that cannot name their runtime are a claim, not evidence of the work."""
        server, thread = _serve_answer(_answer(runtime=None))
        try:
            host, port = server.server_address[0], server.server_address[1]
            outcome = HttpNodeBackend(
                base_url=f"http://{host}:{port}", token=TOKEN, node_name="node_other"
            ).execute(request())
        finally:
            stop(server, thread)

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "answer_without_runtime"
        assert outcome.errors[0].error_class is ErrorClass.NEEDS_INPUT

    def test_an_answer_from_a_machine_this_deployment_did_not_place_is_refused(self) -> None:
        """The listener has to be the declared machine, not merely a machine holding the credential."""
        server, thread = _serve_answer(_answer(executed_on="somebody_elses_box"))
        try:
            host, port = server.server_address[0], server.server_address[1]
            outcome = HttpNodeBackend(
                base_url=f"http://{host}:{port}", token=TOKEN, node_name="node_other"
            ).execute(request())
        finally:
            stop(server, thread)

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "answer_from_another_machine"
        assert outcome.errors[0].error_class is ErrorClass.NEEDS_INPUT

    def test_a_redirect_is_refused_and_never_followed(self) -> None:
        """A redirect would carry the attempt and the credential to a destination nobody declared."""
        seen: list[str] = []

        class Target(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the name is the http.server's
                seen.append(self.path)
                self.send_response(200)
                self.send_header("Content-Length", "0")
                self.end_headers()

        target, target_thread = _serve(Target)

        class Redirecting(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the name is the http.server's
                self.send_response(302)
                self.send_header("Location", f"http://127.0.0.1:{target.server_address[1]}/attempt")
                self.send_header("Content-Length", "0")
                self.end_headers()

        server, thread = _serve(Redirecting)
        try:
            host, port = server.server_address[0], server.server_address[1]
            outcome = HttpNodeBackend(
                base_url=f"http://{host}:{port}", token=TOKEN, node_name="nod_server_01"
            ).execute(request())
        finally:
            stop(server, thread)
            stop(target, target_thread)

        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "node_redirected"
        assert outcome.errors[0].error_class is ErrorClass.NEEDS_INPUT
        assert seen == [], "the credential was not carried to the redirect target"

    def test_a_cage_the_machine_cannot_hold_is_refused(self, tmp_path: Path) -> None:
        """A ceiling the node cannot keep is not a ceiling: the node says so instead of running it."""
        made = make_agent(tmp_path, node_name="node_small", cpu=2, memory_mb=512)
        server, thread = serve(made)
        try:
            host, port = server.server_address[0], server.server_address[1]
            outcome = HttpNodeBackend(
                base_url=f"http://{host}:{port}", token=TOKEN, node_name="node_small"
            ).execute(request(cage=cage(cpu=8, memory_mb=16384)))
        finally:
            stop(server, thread)

        assert outcome.status is ToolResultStatus.POLICY_BLOCKED
        assert outcome.errors[0].code == "node_over_capacity"
        assert outcome.errors[0].error_class is ErrorClass.POLICY_BLOCKED


class TestTheAdHocChannel:
    def test_an_adhoc_attempt_does_not_run_where_the_network_cannot_be_closed(
        self, agent: Any
    ) -> None:
        """§9.6: an ad-hoc step needs a node that can keep it off the network."""
        outcome = backend_for(agent).execute(request(spec=adhoc_spec()))
        assert outcome.status is ToolResultStatus.POLICY_BLOCKED
        assert outcome.errors[0].error_class is ErrorClass.POLICY_BLOCKED
        assert "ad-hoc" in outcome.errors[0].message


class TestALostDelivery:
    """§3.3: an answer lost on the way back costs a round trip, not a second run."""

    def test_a_lost_answer_is_asked_for_again_as_the_same_attempt(self) -> None:
        seen: list[bytes] = []

        class Flaky(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the name is the http.server's
                length = int(self.headers.get("Content-Length") or 0)
                seen.append(self.rfile.read(length))
                if len(seen) == 1:
                    self.close_connection = True
                    return
                body = json.dumps(_answer()).encode("utf-8")
                self.send_response(200)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        server, thread = _serve(Flaky)
        try:
            outcome = _backend_retrying(server).execute(request())
        finally:
            stop(server, thread)

        assert outcome.status is ToolResultStatus.SUCCESS
        assert len(seen) == 2
        assert seen[0] == seen[1], "a redelivery is the same request, not a new attempt"

    def test_deliveries_stop_at_the_configured_retry_count(self) -> None:
        seen: list[bytes] = []

        class Dropping(BaseHTTPRequestHandler):
            def do_POST(self) -> None:  # noqa: N802 - the name is the http.server's
                length = int(self.headers.get("Content-Length") or 0)
                seen.append(self.rfile.read(length))
                self.close_connection = True

        server, thread = _serve(Dropping)
        try:
            outcome = _backend_retrying(server).execute(request())
        finally:
            stop(server, thread)

        assert len(seen) == 2, "one delivery and one retry, not an unbounded loop"
        assert outcome.status is ToolResultStatus.FAILED
        assert outcome.errors[0].code == "node_unreachable"
        assert outcome.errors[0].error_class is ErrorClass.RETRYABLE_FAILURE


def _serve(handler: type[BaseHTTPRequestHandler]) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def _backend_retrying(server: ThreadingHTTPServer) -> HttpNodeBackend:
    host, port = server.server_address[0], server.server_address[1]
    return HttpNodeBackend(
        base_url=f"http://{host}:{port}",
        token=TOKEN,
        node_name="nod_server_01",
        retries=1,
        retry_delay=0.0,
    )


def _serve_answer(document: dict) -> tuple[ThreadingHTTPServer, threading.Thread]:
    """A listener that answers every attempt with the document it was given."""

    class Answering(BaseHTTPRequestHandler):
        def do_POST(self) -> None:  # noqa: N802 - the name is the http.server's
            body = json.dumps(document).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

    return _serve(Answering)


def _answer(
    *,
    attempt: int = 1,
    runtime: Optional[str] = "host-python 3.12.11",
    executed_on: Optional[str] = None,
) -> dict:
    """An answer the far side might send, about the given attempt and with the given runtime."""
    payload = b'{"slot":"counts"}'
    outcome = ExecutionOutcome(
        run_id="run_prj_001_01",
        node_id="qc_001",
        attempt=attempt,
        status=ToolResultStatus.SUCCESS,
        executed_on=executed_on,
        result=ToolResult(
            run_id="run_prj_001_01",
            node_id="qc_001",
            project_id="prj_001",
            tool_name="scanpy_qc",
            tool_version="1.11.0",
            status=ToolResultStatus.SUCCESS,
            execution=ToolExecution(
                container_image_digest="sha256:" + "ab" * 32,
                tool_version="1.11.0",
                parameters_sha256=D64,
            ),
            input_asset_ids=["as_0001"],
            input_hashes={"as_0001": D64},
            output_asset_ids=["as_out"],
        ),
        outputs=[
            ExecutionOutput(
                slot="counts",
                schema_version="counts_v1",
                sha256=sha256_hex(payload),
                content=payload,
            )
        ],
        runtime=runtime,
    )
    return outcome_to_envelope(outcome)
