"""The node's end of the transport: a service that runs what it is handed (plan V2.0 sections 3.3, 4.2, 9.6).

A node runs work for a control plane that is somewhere else, so it cannot trust what it is handed
and it cannot answer in-process: the answer has to travel back as bytes. This module is the node's
half of that exchange - an HTTP service that takes a request envelope, runs the attempt in a child
process inside the cage the request names, and hands back the outcome envelope.

Two things are the node's own and cannot be the control plane's:

* the cage. The limits travel with the request, and the node keeps them where the work happens
  (§9.6). A node that cannot keep a limit says so instead of running the work anyway - and the
  memory ceiling is checked against the machine itself, because a cage the machine cannot hold is
  not a ceiling at all.
* the credential. The node opens its door to the control plane with a shared secret that lives in
  the node's own file and in the control plane's environment. It is never a row in the index and
  never a field of the request.

The transport itself is plain HTTP, so the request and the secret travel in the clear. That is a
statement about the network the node sits on, not about the platform: the agent binds to the
loopback address by default and expects to be reached over a tunnel or a trusted network. A node
exposed to a wider network needs TLS in front of it.
"""

from __future__ import annotations

import argparse
import hmac
import json
import os
import socket
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Optional, Sequence

from bioadapt_schemas.errors import ApiError, ErrorClass

from .enums import ToolResultStatus
from .execution import (
    ExecutionRequest,
    outcome_for_error,
    outcome_to_envelope,
    request_from_envelope,
)
from .process import ProcessBackend

#: Where an attempt is accepted.
ATTEMPT_PATH = "/attempt"

#: Where the shared secret comes from when it is not in a file.
TOKEN_ENV = "BIOADAPT_NODE_TOKEN"


def _total_memory_mb() -> Optional[int]:
    """How much memory this machine has, when it will say.

    Linux answers out of ``/proc``; macOS answers a sysctl. A machine that answers neither is not
    assumed to be small: the agent then trusts the cage rather than refusing work it might have had
    room for.
    """
    try:
        with open("/proc/meminfo", "r", encoding="utf-8") as handle:
            for line in handle:
                if line.startswith("MemTotal:"):
                    return int(line.split()[1]) // 1024
    except (OSError, IndexError, ValueError):
        pass
    try:
        finished = subprocess.run(
            ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, timeout=10
        )
        if finished.returncode == 0 and finished.stdout.strip():
            return int(finished.stdout.strip()) // (1024 * 1024)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return None


class NodeAgent:
    """What this node does with the work it is handed."""

    def __init__(
        self,
        *,
        command: Sequence[str],
        work_root: str | Path,
        token: str,
        node_name: str = "node",
        isolated: bool = False,
        cpu: Optional[int] = None,
        memory_mb: Optional[int] = None,
        retain_hours: Optional[float] = 24.0,
    ) -> None:
        self.token = token
        self.node_name = node_name
        #: How much this machine has. The cage is checked against it, so a request that asks for more
        #: memory than the node owns is refused here rather than answered with an out-of-memory kill.
        self.cpu = cpu if cpu is not None else (os.cpu_count() or 1)
        self.memory_mb = memory_mb if memory_mb is not None else _total_memory_mb()
        self.backend = ProcessBackend(
            command=list(command),
            work_root=work_root,
            node_name=node_name,
            isolated=isolated,
            retain_hours=retain_hours,
        )

    def authorized(self, header: Optional[str]) -> bool:
        """Whether the caller holds the node's secret."""
        if not header or not header.startswith("Bearer "):
            return False
        return hmac.compare_digest(header[len("Bearer ") :].strip(), self.token)

    def answer(self, envelope: dict[str, Any]) -> dict[str, Any]:
        """Run the attempt this envelope describes, and say what came out."""
        request = request_from_envelope(envelope)
        over = self._over_capacity(request)
        if over:
            outcome = outcome_for_error(
                request,
                ToolResultStatus.POLICY_BLOCKED,
                ApiError(
                    code="node_over_capacity",
                    error_class=ErrorClass.POLICY_BLOCKED,
                    message="; ".join(over),
                    field="cage",
                ),
                executed_on=self.node_name,
            )
            return outcome_to_envelope(outcome)
        return outcome_to_envelope(self.backend.execute(request))

    def _over_capacity(self, request: ExecutionRequest) -> list[str]:
        """The parts of the cage this machine cannot hold."""
        reasons: list[str] = []
        if request.cage.cpu > self.cpu:
            reasons.append(
                f"the request allows {request.cage.cpu} cpu and this machine has {self.cpu}"
            )
        if self.memory_mb is not None and request.cage.memory_mb > self.memory_mb:
            reasons.append(
                f"the request allows {request.cage.memory_mb}MB of memory and this machine has "
                f"{self.memory_mb}MB"
            )
        return reasons


def _handler_for(agent: NodeAgent) -> type[BaseHTTPRequestHandler]:
    class Handler(BaseHTTPRequestHandler):
        server_version = "bioadapt-node-agent"

        def do_POST(self) -> None:  # noqa: N802 - the name is the http.server's
            if self.path != ATTEMPT_PATH:
                self._reply(404, {"error": "this node answers attempts and nothing else"})
                return
            if not agent.authorized(self.headers.get("Authorization")):
                self._reply(401, {"error": "the credential was not accepted"})
                return
            length = int(self.headers.get("Content-Length") or 0)
            body = self.rfile.read(length)
            try:
                envelope = json.loads(body)
            except (ValueError, json.JSONDecodeError):
                self._reply(400, {"error": "the body is not json"})
                return
            if not isinstance(envelope, dict):
                self._reply(400, {"error": "the body is not a request"})
                return
            try:
                answer = agent.answer(envelope)
            except ValueError as error:
                self._reply(400, {"error": str(error)})
                return
            except Exception as error:  # a broken attempt must not take the node down with it
                self._reply(500, {"error": f"the node could not run the attempt: {error}"})
                return
            self._reply(200, answer)

        def _reply(self, status: int, body: dict[str, Any]) -> None:
            payload = json.dumps(body).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

    return Handler


def build_server(
    agent: NodeAgent, *, host: str = "127.0.0.1", port: int = 8765
) -> ThreadingHTTPServer:
    """The agent's service, ready to serve. The caller decides when to serve."""
    return ThreadingHTTPServer((host, port), _handler_for(agent))


def _token(token_file: Optional[str]) -> str:
    """The shared secret, from the node's own file or from its environment."""
    if token_file:
        return Path(token_file).read_text(encoding="utf-8").strip()
    from_env = os.environ.get(TOKEN_ENV, "").strip()
    if from_env:
        return from_env
    raise SystemExit(
        f"the agent needs a shared secret: pass --token-file, or set {TOKEN_ENV} in the node's "
        "environment"
    )


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="bioadapt_tool_registry.node_agent",
        description="Run a control plane's attempts on this machine.",
    )
    parser.add_argument(
        "--command",
        nargs="+",
        required=True,
        help="the command that runs one step on this node",
    )
    parser.add_argument("--work-root", required=True, help="where the node keeps its scratch")
    parser.add_argument("--token-file", help="the file holding the shared secret")
    parser.add_argument("--node-name", default=socket.gethostname())
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument(
        "--isolated",
        action="store_true",
        help="the launcher keeps attempts off the network, so an ad-hoc step may run here",
    )
    parser.add_argument(
        "--retain-hours",
        type=float,
        default=24.0,
        help="how long an answered attempt is kept so a redelivery gets the same answer",
    )
    args = parser.parse_args(argv)

    agent = NodeAgent(
        command=args.command,
        work_root=args.work_root,
        token=_token(args.token_file),
        node_name=args.node_name,
        isolated=args.isolated,
        retain_hours=args.retain_hours,
    )
    server = build_server(agent, host=args.host, port=args.port)
    host, port = server.server_address[0], server.server_address[1]
    print(f"node agent {args.node_name} listening on {host}:{port}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
