"""A machine to stand in for a real one in the execution tests (sections 3.3, 4.2, 9.4).

The stand-in is a real agent on a real socket with a real child process behind it, because the things
that go wrong between two machines do not go wrong between two functions: a credential is checked at
the door, and an answer travels back as bytes. What the machine says it has is stated rather than
measured, so a test says the same thing on a laptop and on a rack.
"""

from __future__ import annotations

import sys
import threading
from datetime import UTC, datetime
from http.server import ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional

from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_schemas.nodes import ExecutionNode, NodeCapabilities, NodePosition
from bioadapt_tool_registry import (
    ExecutionCage,
    ExecutionInput,
    ExecutionRequest,
    HttpNodeBackend,
    NodeAgent,
)
from bioadapt_tool_registry.node_agent import build_server

from registry_factories import D64, complete_spec

PAYLOAD = b'{"slot":"counts","schema_version":"counts_v1"}'

#: A node that stands in for the tool on the far side: it reads the request the agent staged and
#: answers with the products it made.
FAKE_NODE = '''
import os

from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_tool_registry.enums import ToolResultStatus
from bioadapt_tool_registry.execution import ExecutionOutcome, ExecutionOutput
from bioadapt_tool_registry.process import read_process_request, write_process_outcome
from bioadapt_tool_registry.result import ToolExecution, ToolResult

work_dir = os.environ["BIOADAPT_WORK_DIR"]
request = read_process_request(work_dir)
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
    attempt=request.attempt,
    status=ToolResultStatus.SUCCESS,
    result=result,
    outputs=[ExecutionOutput(slot="counts", schema_version="counts_v1",
                             sha256=sha256_hex(payload), content=payload)],
)
write_process_outcome(work_dir, outcome)
'''

TOKEN = "a-shared-secret"

#: What a stand-in node says it has. Stated rather than measured, so the test says the same thing
#: wherever it runs.
NODE_CPU = 8
NODE_MEMORY_MB = 16384


def cage(**overrides: Any) -> ExecutionCage:
    fields: dict[str, Any] = {"cpu": NODE_CPU, "memory_mb": NODE_MEMORY_MB, "max_minutes": 240}
    fields.update(overrides)
    return ExecutionCage(**fields)


def request(**overrides: Any) -> ExecutionRequest:
    fields: dict[str, Any] = {
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


def make_agent(tmp_path: Path, **overrides: Any) -> NodeAgent:
    script = tmp_path / "fake_node.py"
    script.write_text(FAKE_NODE, encoding="utf-8")
    fields: dict[str, Any] = {
        "command": [sys.executable, str(script)],
        "work_root": tmp_path / "work",
        "token": TOKEN,
        "node_name": "nod_server_01",
        "cpu": NODE_CPU,
        "memory_mb": NODE_MEMORY_MB,
    }
    fields.update(overrides)
    return NodeAgent(**fields)


def serve(agent: NodeAgent) -> tuple[ThreadingHTTPServer, threading.Thread]:
    server = build_server(agent, host="127.0.0.1", port=0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    return server, thread


def stop(server: ThreadingHTTPServer, thread: threading.Thread) -> None:
    server.shutdown()
    server.server_close()
    thread.join(timeout=10)


def backend_for(agent: Any, **overrides: Any) -> HttpNodeBackend:
    host, port = agent.server.server_address[0], agent.server.server_address[1]
    fields: dict[str, Any] = {
        "base_url": f"http://{host}:{port}",
        "token": TOKEN,
        "node_name": agent.node_name,
    }
    fields.update(overrides)
    return HttpNodeBackend(**fields)


def declared_node(endpoint: Optional[str] = "http://127.0.0.1:9", **overrides: Any) -> ExecutionNode:
    """A machine as a person would declare it. The default address goes nowhere on purpose: a test
    that expects no work to be sent can say so by never reaching a listener."""
    fields: dict[str, Any] = {
        "node_id": "nod_server_01",
        "label": "lab server",
        "position": NodePosition.LOCAL,
        "capabilities": NodeCapabilities(
            cpu=NODE_CPU, memory_mb=NODE_MEMORY_MB, can_run_containers=True
        ),
        "endpoint": endpoint,
        "registered_by": "user_platform_admin_01",
        "registered_at": datetime(2026, 5, 1, tzinfo=UTC),
    }
    fields.update(overrides)
    return ExecutionNode(**fields)


def address_of(agent: Any) -> str:
    host, port = agent.server.server_address[0], agent.server.server_address[1]
    return f"http://{host}:{port}"
