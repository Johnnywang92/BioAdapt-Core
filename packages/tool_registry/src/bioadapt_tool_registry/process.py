"""The node process backend: one attempt, in a child process, inside the cage (§3.3, §4.2, §9.6).

The seam in ``bioadapt_tool_registry.execution`` says what the work is; this module is where the
work happens on a machine that is not the control plane. A node - a laptop, a lab server, a rack of
them - runs the attempt in a child process of its own and hands the products back as bytes, so the
run means the same thing wherever it ran (§4.2).

The cage travels with the request because that is where the limit has to be: a limit the control
plane only remembers is a limit nobody enforces where the work happens (§9.6). Each part of the
cage is either kept here or the attempt does not start:

* the wall clock: the parent waits for the ceiling and stops the attempt's process group when it
  passes;
* the cpu time: the child is started through the launcher, which holds it to the ceiling in cpu
  seconds before the tool runs;
* the memory: the parent watches the child's footprint and stops it when it outgrows the ceiling.
  A plain process cannot be capped in memory by the operating system - the address-space limit that
  would do it is refused on macOS and overstates the real footprint elsewhere - so the ceiling is
  kept by a watchdog here, and by the node's container runtime where the node has one.

The network half of the cage is the node's promise rather than this backend's: a plain child
process cannot close a machine's network. An ad-hoc attempt therefore only runs here where the
deployment says the launcher keeps it off the network (§9.6), and a registered tool's own network
policy stays the tool's business (§9.4).

The inputs are staged rather than carried: a node's inputs can be gigabytes, so the request names
the files the bytes were written into and the child reads them from there. The digest of each
input travels with its name, so the child can check what it was handed against what was asked for.

An attempt's scratch is named after the attempt, not after this delivery of it, and the answer stays
there after it is handed back. A control plane that loses the answer to an attempt - a dropped
connection is an ordinary event on a network - redelivers the same request and gets the same answer
instead of a second run (§3.3). The request that produced the answer is kept beside it, so work that
is not the work already answered is refused rather than run twice under one attempt's name.
"""

from __future__ import annotations

import fcntl
import json
import os
import platform
import re
import shutil
import signal
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Callable, Optional, Sequence, TextIO, Union

from bioadapt_schemas.entities import sha256_of_json
from bioadapt_schemas.enums import ToolClass
from bioadapt_schemas.errors import ApiError, ErrorClass
from bioadapt_schemas.identifiers import sha256_hex
from bioadapt_tool_registry.enums import ToolResultStatus

from .execution import (
    PROTOCOL_VERSION,
    ExecutionCage,
    ExecutionInput,
    ExecutionOutcome,
    ExecutionRequest,
    outcome_for_error,
    outcome_from_envelope,
    outcome_to_envelope,
    request_to_envelope,
    require_protocol,
)
from .spec import ToolSpec

#: Where the parent tells the child which directory the attempt lives in.
WORK_DIR_ENV = "BIOADAPT_WORK_DIR"
REQUEST_FILE = "request.json"
#: The digest of the request that produced the answer here, so a redelivery can be told apart from
#: different work sent under the same attempt's name.
REQUEST_DIGEST_FILE = "request.sha256"
OUTCOME_FILE = "outcome.json"
#: Held while this process is answering the attempt; a second delivery does not start it again.
LOCK_FILE = "attempt.lock"
STDOUT_FILE = "stdout.log"
STDERR_FILE = "stderr.log"
INPUTS_DIR = "inputs"

#: How a minute of the cage is counted, and how often the parent looks at the child.
SECONDS_PER_MINUTE = 60.0
SECONDS_PER_HOUR = 3600.0
SAMPLE_SECONDS = 0.2

#: What a worker gets from the host's environment. The node's own credential and every other secret
#: live outside this set on purpose: a step that can read the secret that opens its node can act as
#: the node. A deployment that needs more names passes them in ``env_passthrough``.
BASE_ENV = (
    "PATH",
    "HOME",
    "LANG",
    "LC_ALL",
    "LC_CTYPE",
    "TMPDIR",
    "TMP",
    "TEMP",
    "PYTHONPATH",
    "PYTHONDONTWRITEBYTECODE",
    "PYTHONUNBUFFERED",
    "SSL_CERT_FILE",
    "SSL_CERT_DIR",
    "SYSTEMROOT",
)

_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")


def _safe(name: str) -> str:
    """A name that can be a directory name without carrying anything of the original's shape."""
    return _UNSAFE.sub("_", name)[:64] or "unnamed"


def _request_digest(request: ExecutionRequest) -> str:
    """The whole request as one digest, so work that differs is never mistaken for a redelivery."""
    return sha256_of_json(request_to_envelope(request))


def write_process_request(work_dir: Union[str, Path], request: ExecutionRequest) -> Path:
    """Stage the request for the child: the bytes beside it, the names inside it."""
    root = Path(work_dir)
    (root / INPUTS_DIR).mkdir(parents=True, exist_ok=True)
    inputs = []
    for index, item in enumerate(request.inputs):
        name = f"{index:02d}-{_safe(item.slot)}.bin"
        (root / INPUTS_DIR / name).write_bytes(item.content)
        inputs.append(
            {
                "slot": item.slot,
                "asset_id": item.asset_id,
                "sha256": item.sha256,
                "schema_version": item.schema_version,
                "uri": item.uri,
                "path": f"{INPUTS_DIR}/{name}",
            }
        )
    envelope = {
        "protocol": PROTOCOL_VERSION,
        "run_id": request.run_id,
        "node_id": request.node_id,
        "attempt": request.attempt,
        "project_id": request.project_id,
        "spec": request.spec.model_dump(mode="json"),
        "parameters": request.parameters,
        "parameters_sha256": request.parameters_sha256,
        "cage": request.cage.model_dump(mode="json"),
        "inputs": inputs,
    }
    path = root / REQUEST_FILE
    path.write_text(json.dumps(envelope, sort_keys=True, separators=(",", ":")), encoding="utf-8")
    return path


def read_process_request(work_dir: Union[str, Path]) -> ExecutionRequest:
    """Read the staged request back, insisting the bytes are the bytes the request named."""
    root = Path(work_dir)
    envelope = json.loads((root / REQUEST_FILE).read_text(encoding="utf-8"))
    require_protocol(envelope, "request")
    inputs = []
    for item in envelope.get("inputs", []):
        content = (root / item["path"]).read_bytes()
        if sha256_hex(content) != item["sha256"]:
            raise ValueError(f"staged input {item['slot']!r} does not hash to the digest the request named")
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


def write_process_outcome(work_dir: Union[str, Path], outcome: ExecutionOutcome) -> Path:
    """Write the answer where the parent will find it, whole or not at all.

    A parent watching for the file must never read a half-written answer and call it torn: writing
    beside it and renaming into place makes the answer appear in one step.
    """
    root = Path(work_dir)
    path = root / OUTCOME_FILE
    staged = root / f".{OUTCOME_FILE}.{uuid.uuid4().hex[:8]}.tmp"
    staged.write_text(
        json.dumps(outcome_to_envelope(outcome), sort_keys=True, separators=(",", ":")), encoding="utf-8"
    )
    os.replace(staged, path)
    return path


def read_process_outcome(work_dir: Union[str, Path]) -> ExecutionOutcome:
    """Read the answer back, insisting the products are the bytes the answer names."""
    envelope = json.loads((Path(work_dir) / OUTCOME_FILE).read_text(encoding="utf-8"))
    return outcome_from_envelope(envelope)


def _resident_bytes(pid: int) -> int:
    """The child's footprint right now, in bytes.

    Linux reads it out of ``/proc``; macOS has no ``/proc``, so the child's own stat is asked for.
    A platform that answers neither reports nothing, and the watchdog then watches the clock alone.
    """
    try:
        with open(f"/proc/{pid}/statm", "r", encoding="utf-8") as handle:
            pages = int(handle.read().split()[1])
        return pages * os.sysconf("SC_PAGE_SIZE")
    except (OSError, IndexError, ValueError):
        pass
    try:
        finished = subprocess.run(
            ["ps", "-o", "rss=", "-p", str(pid)], capture_output=True, text=True, timeout=10
        )
    except (OSError, subprocess.SubprocessError):
        return 0
    if finished.returncode != 0 or not finished.stdout.strip():
        return 0
    try:
        return int(finished.stdout.split()[0]) * 1024
    except (IndexError, ValueError):
        return 0


def _stop(process: subprocess.Popen) -> None:
    """Stop the attempt and everything it started."""
    try:
        os.killpg(os.getpgid(process.pid), signal.SIGKILL)
    except (ProcessLookupError, PermissionError, OSError):
        process.kill()
    try:
        process.wait(timeout=10)
    except subprocess.TimeoutExpired:  # pragma: no cover - the kill above does not leave it alive
        pass


def _tail(path: Path, limit: int = 400) -> str:
    try:
        text = path.read_text(encoding="utf-8", errors="replace").strip()
    except OSError:
        return ""
    return text[-limit:]


class ProcessBackend:
    """Runs one attempt in a child process on this node, inside the cage the request names."""

    def __init__(
        self,
        *,
        command: Sequence[str],
        work_root: Union[str, Path],
        node_name: str = "local",
        isolated: bool = False,
        retain_hours: Optional[float] = 24.0,
        env_passthrough: Sequence[str] = (),
        clock: Optional[Callable[[], datetime]] = None,
    ) -> None:
        self.command = list(command)
        self.work_root = Path(work_root)
        #: Which machine this is, so the answer says where the work ran (§3.3).
        self.node_name = node_name
        #: Whether the launcher keeps the attempt off the network (§9.6). A plain child process
        #: cannot, so a node without a container runtime does not set this.
        self.isolated = isolated
        #: Extra host variables the deployment lets the worker see. The base set is the system's
        #: basics; the node's credential is deliberately not among them, so a step cannot read the
        #: secret that opens its own node unless the deployment names it here on purpose.
        self.env_passthrough = tuple(env_passthrough)
        #: How long an answered attempt stays on disk so a redelivery can be answered from it. None
        #: keeps every answer until a person removes it.
        if retain_hours is not None and retain_hours < 0:
            raise ValueError("an attempt cannot be kept for a negative number of hours")
        self.retain_hours = retain_hours
        #: What an answer that does not speak for itself actually ran: a plain Python process on this
        #: machine, never the image the tool declares (§9.4).
        self.runtime = f"host-python {platform.python_version()}"
        self.clock = clock or (lambda: datetime.now(UTC))

    # ------------------------------------------------------------------ the seam

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        refusal = self._refusal(request)
        if refusal is not None:
            return refusal
        self._prune()
        work_dir = self._work_dir(request)
        try:
            holding = self._hold(work_dir)
        except BlockingIOError:
            return self._outcome(
                request,
                ToolResultStatus.FAILED,
                ApiError(
                    code="attempt_in_flight",
                    error_class=ErrorClass.RETRYABLE_FAILURE,
                    message=(
                        f"attempt {request.attempt} of {request.run_id}/{request.node_id} is already "
                        "being answered on this node; the same attempt is never run twice"
                    ),
                    field="attempt",
                ),
            )
        with holding:
            replay = self._replay(request, work_dir)
            if replay is not None:
                return replay
            started_at = self.clock()
            write_process_request(work_dir, request)
            (work_dir / REQUEST_DIGEST_FILE).write_text(_request_digest(request), encoding="utf-8")
            outcome = self._run(request, work_dir, started_at)
            #: The answer stays where a redelivery of this attempt will find it, whatever it says:
            #: one attempt has one answer, and a lost response is not a reason to run the work again.
            write_process_outcome(work_dir, outcome)
            return outcome

    # ------------------------------------------------------------------ answering a redelivery

    def _hold(self, work_dir: Path) -> TextIO:
        """Hold this attempt for as long as this process is answering it."""
        handle = (work_dir / LOCK_FILE).open("a+")
        try:
            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            handle.close()
            raise
        return handle

    def _replay(self, request: ExecutionRequest, work_dir: Path) -> Optional[ExecutionOutcome]:
        """The answer this node already gave for this attempt, when it gave one (§3.3)."""
        recorded = work_dir / REQUEST_DIGEST_FILE
        if not recorded.exists():
            if (work_dir / OUTCOME_FILE).exists():
                return self._outcome(
                    request,
                    ToolResultStatus.FAILED,
                    ApiError(
                        code="answer_unverifiable",
                        error_class=ErrorClass.NEEDS_INPUT,
                        message=(
                            f"this node holds an answer for attempt {request.attempt} of "
                            f"{request.run_id}/{request.node_id} but not the request that produced it, "
                            "so it cannot say whether this delivery is the same work"
                        ),
                        field="attempt",
                    ),
                )
            return None
        if recorded.read_text(encoding="utf-8").strip() != _request_digest(request):
            return self._outcome(
                request,
                ToolResultStatus.FAILED,
                ApiError(
                    code="attempt_conflict",
                    error_class=ErrorClass.CONFLICT,
                    message=(
                        f"this node already took different work for attempt {request.attempt} of "
                        f"{request.run_id}/{request.node_id}; one attempt has one answer"
                    ),
                    field="attempt",
                ),
            )
        if not (work_dir / OUTCOME_FILE).exists():
            return None
        try:
            outcome = read_process_outcome(work_dir)
        except (ValueError, json.JSONDecodeError) as error:
            return self._outcome(
                request,
                ToolResultStatus.FAILED,
                ApiError(
                    code="answer_unreadable",
                    error_class=ErrorClass.NEEDS_INPUT,
                    message=(
                        f"the answer this node kept for attempt {request.attempt} of "
                        f"{request.run_id}/{request.node_id} does not read as an answer: {error}"
                    ),
                    field="attempt",
                ),
            )
        if outcome.key != request.key:
            return self._outcome(
                request,
                ToolResultStatus.FAILED,
                ApiError(
                    code="answer_mismatch",
                    error_class=ErrorClass.FAILED,
                    message=(
                        f"the kept answer names {outcome.node_id}@{outcome.attempt} of run "
                        f"{outcome.run_id}, and this attempt is {request.node_id}@{request.attempt} "
                        f"of run {request.run_id}"
                    ),
                    field="attempt",
                ),
            )
        return outcome.model_copy(update={"executed_on": self.node_name})

    def _prune(self) -> None:
        """Remove attempts kept past the retention window, leaving the ones still being answered."""
        if self.retain_hours is None or not self.work_root.exists():
            return
        cutoff = self.clock().timestamp() - self.retain_hours * SECONDS_PER_HOUR
        for path in self.work_root.glob("*/*/attempt-*"):
            try:
                if path.stat().st_mtime >= cutoff:
                    continue
            except OSError:
                continue
            try:
                with (path / LOCK_FILE).open("a+") as handle:
                    try:
                        fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                    except BlockingIOError:
                        continue
                    shutil.rmtree(path, ignore_errors=True)
            except OSError:
                continue

    # ------------------------------------------------------------------ refusing

    def _refusal(self, request: ExecutionRequest) -> Optional[ExecutionOutcome]:
        """The reasons this attempt may not start here, if there are any."""
        reasons = list(request.cage.holds(request.spec))
        if request.spec.tool_class is ToolClass.PROJECT_ADHOC:
            if request.cage.allow_network:
                reasons.append("an ad-hoc attempt may not have an outbound route (section 9.6)")
            elif not self.isolated:
                reasons.append(
                    "this node starts plain processes, and cannot keep an ad-hoc attempt off the network"
                )
        if not reasons:
            return None
        error = ApiError(
            code="cage_refused",
            error_class=ErrorClass.POLICY_BLOCKED,
            message="; ".join(reasons),
            field="cage",
        )
        return self._outcome(request, ToolResultStatus.POLICY_BLOCKED, error)

    # ------------------------------------------------------------------ running

    def _work_dir(self, request: ExecutionRequest) -> Path:
        """The attempt's own scratch, named after the attempt rather than after this delivery."""
        path = (
            self.work_root
            / _safe(request.run_id)
            / _safe(request.node_id)
            / f"attempt-{request.attempt}"
        )
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _run(self, request: ExecutionRequest, work_dir: Path, started_at: datetime) -> ExecutionOutcome:
        #: The cage's minutes are wall-clock minutes, the budget the plan approved. The kernel's CPU
        #: clock counts every core the step may use, so the ceiling is the wall budget times the
        #: cores: a four-core step is not killed at a quarter of the time it was promised (§9.6).
        cpu_seconds = int(request.cage.max_minutes * SECONDS_PER_MINUTE * max(1, request.cage.cpu))
        command = [
            sys.executable,
            "-m",
            "bioadapt_tool_registry.limits",
            "--cpu-seconds",
            str(cpu_seconds),
            "--",
            *self.command,
        ]
        env = {
            key: os.environ[key]
            for key in (*BASE_ENV, *self.env_passthrough)
            if key in os.environ
        }
        env[WORK_DIR_ENV] = str(work_dir)
        began = time.monotonic()
        with (work_dir / STDOUT_FILE).open("wb") as out, (work_dir / STDERR_FILE).open("wb") as err:
            process = subprocess.Popen(
                command,
                cwd=work_dir,
                env=env,
                stdin=subprocess.DEVNULL,
                stdout=out,
                stderr=err,
                start_new_session=True,
            )
            stop = self._watch(process, request, work_dir)
        if stop is not None:
            return stop
        finished_at = self.clock()
        outcome = self._answer(request, work_dir, began)
        if outcome is not None:
            return outcome
        return self._outcome(
            request,
            ToolResultStatus.FAILED,
            ApiError(
                code="attempt_died",
                error_class=ErrorClass.FAILED,
                message=self._death_message(process, work_dir),
                field="attempt",
            ),
            started_at=started_at,
            finished_at=finished_at,
        )

    def _watch(
        self, process: subprocess.Popen, request: ExecutionRequest, work_dir: Path
    ) -> Optional[ExecutionOutcome]:
        """Hold the attempt inside the cage while it runs; answer when it breaks out."""
        deadline = time.monotonic() + request.cage.max_minutes * SECONDS_PER_MINUTE
        ceiling = request.cage.memory_mb * 1024 * 1024
        while True:
            try:
                process.wait(timeout=SAMPLE_SECONDS)
                return None
            except subprocess.TimeoutExpired:
                pass
            if time.monotonic() > deadline:
                _stop(process)
                return self._outcome(
                    request,
                    ToolResultStatus.FAILED,
                    ApiError(
                        code="attempt_too_long",
                        error_class=ErrorClass.RESOURCE_EXCEEDED,
                        message=(
                            f"the attempt ran past its ceiling of {request.cage.max_minutes} minutes and "
                            "was stopped"
                        ),
                        field="cage",
                    ),
                )
            resident = _resident_bytes(process.pid)
            if resident > ceiling:
                _stop(process)
                return self._outcome(
                    request,
                    ToolResultStatus.FAILED,
                    ApiError(
                        code="attempt_too_large",
                        error_class=ErrorClass.RESOURCE_EXCEEDED,
                        message=(
                            f"the attempt grew past its ceiling of {request.cage.memory_mb}MB and was stopped"
                        ),
                        field="cage",
                    ),
                )

    def _answer(self, request: ExecutionRequest, work_dir: Path, began: float) -> Optional[ExecutionOutcome]:
        """The answer the child wrote, when it wrote one about this attempt."""
        if not (work_dir / OUTCOME_FILE).exists():
            return None
        try:
            outcome = read_process_outcome(work_dir)
        except (ValueError, json.JSONDecodeError):
            return None
        if outcome.key != request.key:
            return self._outcome(
                request,
                ToolResultStatus.FAILED,
                ApiError(
                    code="answer_mismatch",
                    error_class=ErrorClass.FAILED,
                    message=(
                        f"the answer names {outcome.node_id}@{outcome.attempt} of run {outcome.run_id}, "
                        f"and this attempt is {request.node_id}@{request.attempt} of run {request.run_id}"
                    ),
                    field="attempt",
                ),
            )
        updates: dict[str, Any] = {
            "executed_on": self.node_name,
            "compute_minutes": round((time.monotonic() - began) / SECONDS_PER_MINUTE, 6),
        }
        #: A child that names its own runtime (the reference worker does) is believed about it; one
        #: that does not is a plain host process, and the answer says so rather than leaving the
        #: declared image digest to stand for the runtime (§9.4).
        if not outcome.runtime:
            updates["runtime"] = self.runtime
        return outcome.model_copy(update=updates)

    def _death_message(self, process: subprocess.Popen, work_dir: Path) -> str:
        tail = _tail(work_dir / STDERR_FILE)
        return f"the attempt exited with code {process.returncode}" + (f": {tail}" if tail else "")

    # ------------------------------------------------------------------ answering

    def _outcome(
        self,
        request: ExecutionRequest,
        status: ToolResultStatus,
        error: ApiError,
        *,
        started_at: Optional[datetime] = None,
        finished_at: Optional[datetime] = None,
    ) -> ExecutionOutcome:
        """An answer the control plane can record: a classified result, not an exception."""
        return outcome_for_error(
            request,
            status,
            error,
            executed_on=self.node_name,
            started_at=started_at,
            finished_at=finished_at,
        )
