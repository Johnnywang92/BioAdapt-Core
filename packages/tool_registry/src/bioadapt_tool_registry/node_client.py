"""The control plane's side of the node transport (plan V2.0 sections 3.3, 4.2).

A node is another machine, so the work has to reach it as bytes rather than as a call. The seam
already says what the work is; this module says how it travels: the request envelope goes out over
HTTP and the answer comes back the same way. Nothing about the work changes on the way - the node
runs the same step, in the same cage, and hands back the same bytes, so a run means the same thing
wherever it ran (§4.2).

What the transport adds is failure. A node that is switched off, a connection that drops and a node
that refuses the control plane's credential are ordinary events on a network, and none of them is
an exception a run can absorb: each comes back as a classified result, so the run parks somewhere a
person can see it rather than crashing the engine that was driving it (§3.3).

A delivery that is lost after the node took it is retried as the same attempt, not as a new one. The
node keeps the answer it gave for an attempt and hands the same answer back, so a dropped response
costs a round trip rather than a second run (§3.3).

The credential is the deployment's, not the platform's. It belongs in the control plane's
environment and in the node's own file, and never in a row of the index.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Any, Optional

from bioadapt_schemas.errors import ApiError, ErrorClass

from .enums import ToolResultStatus
from .execution import (
    PROTOCOL_VERSION,
    ExecutionOutcome,
    ExecutionRequest,
    outcome_for_error,
    outcome_from_envelope,
    request_to_envelope,
)

#: Where the attempt is accepted.
ATTEMPT_PATH = "/attempt"

#: Where the node says who is listening, without being handed any work.
HEALTH_PATH = "/healthz"

#: How long a health check waits when the caller did not set a timeout.
PROBE_SECONDS = 5.0

#: How long the client waits past the cage's own ceiling before it calls the node unreachable. The
#: cage is the work's own deadline, so a node that answers later than that has already failed.
SLACK_SECONDS = 30.0


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """A redirect is a third party choosing where the credential and the attempt go next."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


class HttpNodeBackend:
    """Runs an attempt on a node that is another machine.

    The node is named by its address; the run is not decided here. What this backend does is carry
    one request to that address and bring one answer back, refusing to pretend that an answer about
    some other attempt is an answer about this one (§3.3).
    """

    def __init__(
        self,
        *,
        base_url: str,
        token: str,
        node_name: str = "node",
        timeout: Optional[float] = None,
        retries: int = 2,
        retry_delay: float = 0.5,
    ) -> None:
        if retries < 0:
            raise ValueError("a delivery cannot be retried a negative number of times")
        self.base_url = base_url.rstrip("/")
        self.token = token
        #: Which machine this is, for the answer the control plane keeps when the node cannot say.
        self.node_name = node_name
        #: How long to wait. Left unset, the cage's own ceiling decides.
        self.timeout = timeout
        #: How many times a lost delivery is handed to the same node again, and how long to wait
        #: between tries. The node answers a redelivery from the record it kept (§3.3).
        self.retries = retries
        self.retry_delay = retry_delay
        #: Redirects are refused rather than followed: the declared endpoint is the machine this
        #: deployment chose, and a redirect would carry the credential somewhere it did not choose.
        self._opener = urllib.request.build_opener(_NoRedirect())

    # ------------------------------------------------------------------ the seam

    def probe(self) -> Optional[str]:
        """Ask who is listening, without handing over any work.

        Returns None when the listener names itself as the declared machine and speaks this side's
        protocol; otherwise the reason it does not. A health check runs no attempt, so a dispatcher
        may move on to the next declared machine without wondering whether work happened here.
        """
        http_request = urllib.request.Request(
            f"{self.base_url}{HEALTH_PATH}",
            headers={"Authorization": f"Bearer {self.token}"},
            method="GET",
        )
        try:
            with self._opener.open(http_request, timeout=self._probe_wait()) as response:
                document = json.loads(response.read())
        except urllib.error.HTTPError as error:
            if error.code in (401, 403):
                return (
                    f"the node {self.node_name} refused the control plane's credential at its health check"
                )
            return f"the node {self.node_name} answered its health check with {error.code}"
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            return f"the node {self.node_name} did not answer its health check: {error}"
        except (ValueError, json.JSONDecodeError):
            return f"the node {self.node_name} answered its health check with something that is not json"
        if not isinstance(document, dict):
            return f"the node {self.node_name} answered its health check with something that is not a record"
        reported = document.get("node")
        if reported != self.node_name:
            return (
                f"the listener answers as {reported!r}, and this deployment declared the machine as "
                f"{self.node_name!r}"
            )
        spoken = document.get("protocol")
        if spoken != PROTOCOL_VERSION:
            return (
                f"the node {self.node_name} speaks protocol {spoken!r} and this control plane speaks "
                f"{PROTOCOL_VERSION}"
            )
        return None

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        lost: Optional[Exception] = None
        for delivery in range(self.retries + 1):
            if delivery:
                time.sleep(self.retry_delay)
            try:
                body = self._post(request)
            except urllib.error.HTTPError as refused:
                return self._refused(request, refused)
            except (urllib.error.URLError, TimeoutError, OSError) as error:
                lost = error
                continue
            try:
                outcome = outcome_from_envelope(json.loads(body))
            except (ValueError, json.JSONDecodeError) as error:
                return self._unreadable(request, error)
            if outcome.key != request.key:
                return self._about_other_work(request, outcome)
            if outcome.executed_on is not None and outcome.executed_on != self.node_name:
                return self._from_another_machine(request, outcome)
            if outcome.status in (ToolResultStatus.SUCCESS, ToolResultStatus.PARTIAL) and not outcome.runtime:
                return self._without_runtime(request, outcome)
            return outcome
        assert lost is not None, "the loop only ends when every delivery was lost"
        return self._unreachable(request, lost)

    def _post(self, request: ExecutionRequest) -> bytes:
        http_request = urllib.request.Request(
            f"{self.base_url}{ATTEMPT_PATH}",
            data=json.dumps(request_to_envelope(request)).encode("utf-8"),
            headers={
                "Authorization": f"Bearer {self.token}",
                "Content-Type": "application/json",
            },
            method="POST",
        )
        with self._opener.open(http_request, timeout=self._wait(request)) as response:
            return response.read()

    # ------------------------------------------------------------------ the failures

    def _probe_wait(self) -> float:
        return self.timeout if self.timeout is not None else PROBE_SECONDS

    def _wait(self, request: ExecutionRequest) -> float:
        if self.timeout is not None:
            return self.timeout
        return request.cage.max_minutes * 60.0 + SLACK_SECONDS

    def _refused(self, request: ExecutionRequest, error: urllib.error.HTTPError) -> ExecutionOutcome:
        """The node answered, and the answer was that it will not take this work."""
        body = _detail(error)
        if 300 <= error.code < 400:
            return outcome_for_error(
                request,
                ToolResultStatus.FAILED,
                ApiError(
                    code="node_redirected",
                    error_class=ErrorClass.NEEDS_INPUT,
                    message=(
                        f"the node {self.node_name} answered with a redirect"
                        + (f" to {error.headers.get('Location')}" if error.headers.get("Location") else "")
                        + "; this control plane does not follow a redirect with its credential"
                    ),
                    field="node",
                ),
                executed_on=self.node_name,
            )
        if error.code in (401, 403):
            return outcome_for_error(
                request,
                ToolResultStatus.POLICY_BLOCKED,
                ApiError(
                    code="node_refused_credentials",
                    error_class=ErrorClass.POLICY_BLOCKED,
                    message=(
                        f"the node {self.node_name} refused the control plane's credential"
                        + (f": {body}" if body else "")
                    ),
                    field="node",
                ),
                executed_on=self.node_name,
            )
        if error.code == 400:
            return outcome_for_error(
                request,
                ToolResultStatus.FAILED,
                ApiError(
                    code="request_refused",
                    error_class=ErrorClass.NEEDS_INPUT,
                    message=(
                        f"the node {self.node_name} could not read the request"
                        + (f": {body}" if body else "")
                    ),
                    field="request",
                ),
                executed_on=self.node_name,
            )
        return outcome_for_error(
            request,
            ToolResultStatus.FAILED,
            ApiError(
                code="node_failed",
                error_class=ErrorClass.RETRYABLE_FAILURE,
                message=f"the node {self.node_name} answered with {error.code}" + (f": {body}" if body else ""),
                field="node",
            ),
            executed_on=self.node_name,
        )

    def _unreachable(self, request: ExecutionRequest, error: Exception) -> ExecutionOutcome:
        deliveries = self.retries + 1
        noun = "delivery" if deliveries == 1 else "deliveries"
        return outcome_for_error(
            request,
            ToolResultStatus.FAILED,
            ApiError(
                code="node_unreachable",
                error_class=ErrorClass.RETRYABLE_FAILURE,
                message=(
                    f"the node {self.node_name} did not answer after {deliveries} {noun}: {error}"
                ),
                field="node",
            ),
            executed_on=self.node_name,
        )

    def _unreadable(self, request: ExecutionRequest, error: Exception) -> ExecutionOutcome:
        return outcome_for_error(
            request,
            ToolResultStatus.FAILED,
            ApiError(
                code="answer_malformed",
                error_class=ErrorClass.NEEDS_INPUT,
                message=(
                    f"the node {self.node_name} answered with something that does not read as an answer: "
                    f"{error}"
                ),
                field="attempt",
            ),
            executed_on=self.node_name,
        )

    def _about_other_work(self, request: ExecutionRequest, outcome: ExecutionOutcome) -> ExecutionOutcome:
        return outcome_for_error(
            request,
            ToolResultStatus.FAILED,
            ApiError(
                code="answer_mismatch",
                error_class=ErrorClass.FAILED,
                message=(
                    f"the node {self.node_name} answered about {outcome.node_id}@{outcome.attempt} of "
                    f"run {outcome.run_id}, and this attempt is {request.node_id}@{request.attempt} of "
                    f"run {request.run_id}"
                ),
                field="attempt",
            ),
            executed_on=self.node_name,
        )

    def _from_another_machine(
        self, request: ExecutionRequest, outcome: ExecutionOutcome
    ) -> ExecutionOutcome:
        """The answer names a machine this deployment did not choose to send the work to."""
        return outcome_for_error(
            request,
            ToolResultStatus.FAILED,
            ApiError(
                code="answer_from_another_machine",
                error_class=ErrorClass.NEEDS_INPUT,
                message=(
                    f"the listener at {self.base_url} answered as {outcome.executed_on!r}, and this "
                    f"deployment declared the machine as {self.node_name!r}; the answer is not taken "
                    "from a machine the control plane did not place this work on"
                ),
                field="node",
            ),
            executed_on=self.node_name,
        )

    def _without_runtime(self, request: ExecutionRequest, outcome: ExecutionOutcome) -> ExecutionOutcome:
        """Products that cannot say what produced them are not evidence of the work (§9.4)."""
        return outcome_for_error(
            request,
            ToolResultStatus.FAILED,
            ApiError(
                code="answer_without_runtime",
                error_class=ErrorClass.NEEDS_INPUT,
                message=(
                    f"the node {self.node_name} answered with {len(outcome.outputs)} product(s) but did "
                    "not say what ran them; a receipt that leaves the declared image digest to stand for "
                    "the runtime records a claim, not the work"
                ),
                field="attempt",
            ),
            executed_on=self.node_name,
        )


def _detail(error: urllib.error.HTTPError) -> str:
    """What the node said, in its own words, when it said anything."""
    try:
        body = error.read().decode("utf-8", errors="replace")
    except (OSError, ValueError):
        return ""
    try:
        document: Any = json.loads(body)
    except (ValueError, json.JSONDecodeError):
        return body.strip()[:400]
    if isinstance(document, dict):
        return str(document.get("error", ""))[:400]
    return body.strip()[:400]
