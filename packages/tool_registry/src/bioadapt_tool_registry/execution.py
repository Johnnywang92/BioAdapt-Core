"""The execution seam (plan V2.0 sections 3.3, 4.2, 9.4, 9.6, 9.7).

Section 3.3 makes the state machine the only truth about a run and says that a callback must be
idempotent and correlated by ``run_id + node_id + attempt``. This module is the shape such a callback
takes: a request that can be handed to a machine, and an outcome that comes back addressed to the
exact attempt that produced it.

Two properties make the seam usable across a process boundary:

* every byte that crosses it is content, not a reference to a process's memory, so a node can be
  another machine rather than another function; and
* the request names what the work *is* (tool, inputs, parameters, cage) and nothing about where it
  runs, so the same request can be dispatched to a laptop, to a lab server, or to a rack of them
  without the work changing.

The cage travels with the request because that is where the limit has to be: a limit the control
plane only remembers is a limit nobody enforces where the work happens (§9.6).
"""

from __future__ import annotations

import base64
from datetime import datetime
from typing import Any, Optional, Protocol

from pydantic import Field, field_validator, model_validator

from bioadapt_schemas.entities import ImmutableContract, require_hex64, sha256_of_json
from bioadapt_schemas.errors import ApiError
from bioadapt_schemas.identifiers import sha256_hex

from .enums import NetworkMode, ToolResultStatus
from .result import ToolExecution, ToolResult
from .spec import ToolSpec

#: The version of the request/outcome envelopes. A node and a control plane that speak different
#: versions must not try to guess what the other meant: a field this side does not know is a field
#: it would silently drop, and a run that crossed that boundary is not the run that was approved.
PROTOCOL_VERSION = 1


class ExecutionCage(ImmutableContract):
    """The limits one attempt runs inside.

    These are ceilings, not requests: a tool asks for what it needs, and the cage says what it is
    allowed to have. A request that does not fit inside the cage is refused before anything starts,
    and the node that runs it holds the same ceiling while it runs (§9.6).
    """

    cpu: int = Field(ge=1)
    memory_mb: int = Field(ge=1)
    max_minutes: int = Field(ge=1)
    #: Whether the attempt may open an outbound route. An ad-hoc attempt may not (§9.6).
    allow_network: bool = False
    #: Whether the attempt may use a GPU. An ad-hoc attempt may not (§9.6).
    allow_gpu: bool = False

    def holds(self, spec: ToolSpec) -> list[str]:
        """The requirements of this tool that do not fit inside the cage."""
        resources = spec.resources
        found: list[str] = []
        if resources.cpu > self.cpu:
            found.append(f"the tool wants {resources.cpu} cpu, the cage allows {self.cpu}")
        # Memory is judged by the measured peak, the same figure the plan's own floor is built from
        # (§9.4); the declared ceiling is what the tool may burst to, not what it needs.
        needed_memory = resources.measured_peak_memory_mb or resources.memory_mb
        if needed_memory > self.memory_mb:
            found.append(f"the tool needs {needed_memory}MB of memory, the cage allows {self.memory_mb}MB")
        if resources.max_minutes > self.max_minutes:
            found.append(f"the tool wants {resources.max_minutes}min, the cage allows {self.max_minutes}min")
        if resources.gpu > 0 and not self.allow_gpu:
            found.append("the tool wants a GPU, and the cage allows none")
        if not self.allow_network and spec.network.mode is not NetworkMode.CLOSED:
            found.append("the tool needs an outbound route, and the cage allows none")
        return found


class ExecutionInput(ImmutableContract):
    """One input the attempt reads.

    The bytes travel with the request rather than a pointer to them, because the machine that runs
    the work is not necessarily the machine that holds the bytes - and because a node that fetches
    its own data (section 9.7) must still be handed something it can check.
    """

    slot: str
    asset_id: str
    sha256: str
    schema_version: str
    #: Where the bytes came from, so the outcome can say what was read.
    uri: str
    content: bytes


class ExecutionRequest(ImmutableContract):
    """One node's work, addressed so that it can be answered exactly once."""

    run_id: str
    node_id: str
    attempt: int = Field(ge=1)
    project_id: str
    spec: ToolSpec
    parameters: dict[str, Any] = Field(default_factory=dict)
    #: The digest of those parameters, so the machine that runs them can say what it ran with and can
    #: check that what it was handed is what was asked for (section 4.1).
    parameters_sha256: str
    inputs: list[ExecutionInput] = Field(default_factory=list)
    cage: ExecutionCage

    @field_validator("parameters_sha256")
    @classmethod
    def _digest_is_hex(cls, value: str) -> str:
        return require_hex64(value, "parameters digest")

    @property
    def key(self) -> tuple[str, str, int]:
        """The correlation key of section 3.3."""
        return (self.run_id, self.node_id, self.attempt)


class ExecutionOutput(ImmutableContract):
    """One product the attempt produced, as bytes.

    The bytes are the product's own canonical form, so the machine that wrote them and the machine
    that stores them agree on what the product is without either of them interpreting it (§4.1).
    """

    slot: str
    schema_version: str
    sha256: str
    content: bytes


class ExecutionOutcome(ImmutableContract):
    """What one attempt came back with."""

    run_id: str
    node_id: str
    attempt: int = Field(ge=1)
    status: ToolResultStatus
    #: The tool's own verdict, built by whoever ran it.
    result: ToolResult
    outputs: list[ExecutionOutput] = Field(default_factory=list)
    compute_minutes: float = 0.0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    #: Which machine ran it. Empty when the work ran inside the control plane.
    executed_on: Optional[str] = None
    #: What the machine says it actually ran - worker and interpreter, reported by the process that
    #: did the work. The tool's declared image digest is not this: a deployment may run the reference
    #: steps from an installed package, and the record must say so rather than imply a container.
    runtime: Optional[str] = None
    errors: list[ApiError] = Field(default_factory=list)

    @model_validator(mode="after")
    def _addressed_to_one_attempt(self) -> "ExecutionOutcome":
        if (self.result.run_id, self.result.node_id) != (self.run_id, self.node_id):
            raise ValueError("the outcome and the result it carries name different work")
        return self

    @property
    def key(self) -> tuple[str, str, int]:
        return (self.run_id, self.node_id, self.attempt)


class ExecutionBackend(Protocol):
    """Somewhere the work can run.

    A backend is told what to do and answers with what happened. It does not decide whether the run
    may move - that is the state machine's act (§3.3) - and it does not decide whether the work is
    allowed: the request it is handed already carries the ceiling the attempt runs inside.
    """

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome: ...


def execution_key(run_id: str, node_id: str, attempt: int) -> str:
    """The correlation key of section 3.3, as one string."""
    return f"{run_id}:{node_id}:{attempt}"


def outcome_for_error(
    request: ExecutionRequest,
    status: ToolResultStatus,
    error: ApiError,
    *,
    executed_on: Optional[str] = None,
    started_at: Optional[datetime] = None,
    finished_at: Optional[datetime] = None,
) -> ExecutionOutcome:
    """An answer that says the work did not happen, in the shape the driver can absorb.

    A refusal, a node that could not be reached and an attempt that died all come back this way. The
    driver records a classified result rather than an exception, because an exception is not a thing
    the run's state machine can park (§3.3).
    """
    if not request.inputs:
        raise ValueError(
            "a request with no inputs cannot be answered: no result can name a node that read no "
            "bytes (section 9.4)"
        )
    seed = request.parameters.get("seed")
    result = ToolResult(
        run_id=request.run_id,
        node_id=request.node_id,
        project_id=request.project_id,
        tool_name=request.spec.tool_name,
        tool_version=request.spec.tool_version,
        status=status,
        execution=ToolExecution(
            container_image_digest=request.spec.container_image.digest,
            tool_version=request.spec.tool_version,
            parameters_sha256=request.parameters_sha256,
            seed=seed if isinstance(seed, int) else None,
        ),
        input_asset_ids=[item.asset_id for item in request.inputs],
        input_hashes={item.asset_id: item.sha256 for item in request.inputs},
        parameters=dict(request.parameters),
        errors=[error],
        started_at=started_at,
        finished_at=finished_at,
    )
    return ExecutionOutcome(
        run_id=request.run_id,
        node_id=request.node_id,
        attempt=request.attempt,
        status=status,
        result=result,
        outputs=[],
        started_at=started_at,
        finished_at=finished_at,
        executed_on=executed_on,
        errors=[error],
    )


def outcome_digest(outcome: ExecutionOutcome) -> str:
    """A digest of what came back, so the same answer twice can be told from a different one."""
    return sha256_of_json(
        {
            "status": str(outcome.status),
            "tool": outcome.result.tool_name,
            "version": outcome.result.tool_version,
            "outputs": sorted((item.slot, item.sha256) for item in outcome.outputs),
            "errors": [error.code for error in outcome.errors],
        }
    )


def _b64(data: bytes) -> str:
    return base64.b64encode(data).decode("ascii")


def _unb64(data: str) -> bytes:
    return base64.b64decode(data.encode("ascii"))


def require_protocol(envelope: dict[str, Any], kind: str) -> None:
    """Refuse an envelope this side would have to interpret by guesswork.

    Public because the staged request a node reads is its own envelope shape, and it must refuse a
    version it does not speak just as the wire envelope does.
    """
    spoken = envelope.get("protocol")
    if spoken is None:
        raise ValueError(
            f"this {kind} does not name a protocol version; this side speaks {PROTOCOL_VERSION}"
        )
    if spoken != PROTOCOL_VERSION:
        raise ValueError(
            f"this {kind} speaks protocol {spoken!r} and this side speaks {PROTOCOL_VERSION}: "
            "the two may not guess what the other meant"
        )


def request_to_envelope(request: ExecutionRequest) -> dict[str, Any]:
    """The request as it crosses a process or network boundary."""
    return {
        "protocol": PROTOCOL_VERSION,
        "run_id": request.run_id,
        "node_id": request.node_id,
        "attempt": request.attempt,
        "project_id": request.project_id,
        "spec": request.spec.model_dump(mode="json"),
        "parameters": request.parameters,
        "parameters_sha256": request.parameters_sha256,
        "cage": request.cage.model_dump(mode="json"),
        "inputs": [
            {
                "slot": item.slot,
                "asset_id": item.asset_id,
                "sha256": item.sha256,
                "schema_version": item.schema_version,
                "uri": item.uri,
                "content_b64": _b64(item.content),
            }
            for item in request.inputs
        ],
    }


def request_from_envelope(envelope: dict[str, Any]) -> ExecutionRequest:
    """Read a request back, insisting the bytes are the bytes the request names."""
    require_protocol(envelope, "request")
    inputs = []
    for item in envelope.get("inputs", []):
        content = _unb64(item["content_b64"])
        if sha256_hex(content) != item["sha256"]:
            raise ValueError(f"input {item['slot']!r} does not hash to the digest the envelope names")
        inputs.append(
            ExecutionInput(
                slot=item["slot"],
                asset_id=item["asset_id"],
                sha256=item["sha256"],
                schema_version=item["schema_version"],
                uri=item["uri"],
                content=content,
            )
        )
    return ExecutionRequest(
        run_id=envelope["run_id"],
        node_id=envelope["node_id"],
        attempt=int(envelope["attempt"]),
        project_id=envelope["project_id"],
        spec=ToolSpec.model_validate(envelope["spec"]),
        parameters=envelope.get("parameters", {}),
        parameters_sha256=envelope["parameters_sha256"],
        inputs=inputs,
        cage=ExecutionCage.model_validate(envelope["cage"]),
    )


def outcome_to_envelope(outcome: ExecutionOutcome) -> dict[str, Any]:
    """The outcome as it crosses a process or network boundary."""
    return {
        "protocol": PROTOCOL_VERSION,
        "run_id": outcome.run_id,
        "node_id": outcome.node_id,
        "attempt": outcome.attempt,
        "status": str(outcome.status),
        "result": outcome.result.model_dump(mode="json"),
        "outputs": [
            {
                "slot": item.slot,
                "schema_version": item.schema_version,
                "sha256": item.sha256,
                "content_b64": _b64(item.content),
            }
            for item in outcome.outputs
        ],
        "compute_minutes": outcome.compute_minutes,
        "started_at": outcome.started_at.isoformat() if outcome.started_at else None,
        "finished_at": outcome.finished_at.isoformat() if outcome.finished_at else None,
        "executed_on": outcome.executed_on,
        "runtime": outcome.runtime,
        "errors": [error.model_dump(mode="json") for error in outcome.errors],
    }


def outcome_from_envelope(envelope: dict[str, Any]) -> ExecutionOutcome:
    """Read an outcome back, insisting the products are the bytes the envelope names."""
    require_protocol(envelope, "outcome")
    outputs = []
    for item in envelope.get("outputs", []):
        content = _unb64(item["content_b64"])
        if sha256_hex(content) != item["sha256"]:
            raise ValueError(f"output {item['slot']!r} does not hash to the digest the envelope names")
        outputs.append(
            ExecutionOutput(
                slot=item["slot"],
                schema_version=item["schema_version"],
                sha256=item["sha256"],
                content=content,
            )
        )
    return ExecutionOutcome(
        run_id=envelope["run_id"],
        node_id=envelope["node_id"],
        attempt=int(envelope["attempt"]),
        status=ToolResultStatus(envelope["status"]),
        result=ToolResult.model_validate(envelope["result"]),
        outputs=outputs,
        compute_minutes=float(envelope.get("compute_minutes", 0.0)),
        started_at=datetime.fromisoformat(envelope["started_at"]) if envelope.get("started_at") else None,
        finished_at=datetime.fromisoformat(envelope["finished_at"]) if envelope.get("finished_at") else None,
        executed_on=envelope.get("executed_on"),
        runtime=envelope.get("runtime"),
        errors=[ApiError.model_validate(error) for error in envelope.get("errors", [])],
    )
