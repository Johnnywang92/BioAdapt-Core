"""Sending an attempt to one of the machines this deployment declares (sections 3.3, 4.2, 9.4, 9.6).

Section 9.4 matches a tool against a declaration rather than against a named machine. This module is
that match applied to a real attempt: every declared machine is asked whether it can hold the work,
the ones that cannot say why, and the attempt travels to the first one that can.

One attempt goes to exactly one machine. Retrying is the driver's act, under the tool's own retry
promise, and it makes a new attempt; handing the same attempt to a second machine would let two
machines answer for one attempt, which is the duplicate work section 3.3's one-answer rule exists to
prevent. For the same reason the choice is stable - declared order, not whichever machine answered a
ping first - so a redelivery of an attempt lands where the first delivery went.

The project's privacy tier is part of the question, not a note beside it. A strict-local project's
work may not go to a machine declared in the cloud or to one whose network is open, and a
human-genetic-resource project's work needs a closed network, whoever declared the machine (§4.2,
§4.4). A machine that cannot hold that promise is refused with its reason, exactly like one that is
too small.

A machine that cannot be reached and one that refuses the control plane's credential are answers this
backend records rather than exceptions it raises: the run parks where a person can see it, and the
reason names the machine (§3.3).
"""

from __future__ import annotations

from typing import Optional, Sequence

from bioadapt_schemas.enums import PrivacyTier
from bioadapt_schemas.errors import ApiError, ErrorClass
from bioadapt_schemas.nodes import ExecutionNode

from .enums import ToolResultStatus
from .execution import ExecutionOutcome, ExecutionRequest, outcome_for_error
from .node_client import HttpNodeBackend
from .placement import node_allows_tier, node_fits, node_holds


class NodeDispatch:
    """Runs an attempt on a declared machine, chosen by what each machine says it can hold."""

    def __init__(
        self,
        *,
        nodes: Sequence[ExecutionNode],
        privacy_tier: PrivacyTier,
        token: Optional[str] = None,
        timeout: Optional[float] = None,
    ) -> None:
        #: Ordered by id so the same attempt lands on the same machine on every delivery, and so two
        #: deployments that declared the same machines choose alike.
        self.nodes = sorted(nodes, key=lambda node: node.node_id)
        #: The promise the project made about where its bytes may go. It travels with the choice
        #: rather than being remembered by whichever machine happens to take the work.
        self.privacy_tier = privacy_tier
        #: The deployment's credential for its nodes. None is not "no nodes yet" - it is "no way to
        #: open one", and the dispatcher refuses rather than running the work somewhere else.
        self.token = token
        self.timeout = timeout

    def execute(self, request: ExecutionRequest) -> ExecutionOutcome:
        if not self.nodes:
            return self._refuse(
                request,
                "no_node_declared",
                "no compute node is declared, so there is nowhere to send this attempt; declare the "
                "machine that should run it, or run the work in the control plane (§9.4)",
            )
        if self.token is None:
            return self._refuse(
                request,
                "node_credential_missing",
                f"this deployment declares {len(self.nodes)} compute node(s) but the control plane holds "
                "no credential to reach them; provision the node token before executing a run whose work "
                "has to leave this machine",
            )
        candidates, refusals = self._candidates(request)
        if not candidates:
            return self._refuse(
                request,
                "no_node_fits",
                "no declared machine can take this attempt: " + "; ".join(refusals),
            )
        node = candidates[0]
        endpoint = node.endpoint
        assert endpoint is not None, "a candidate is a node that named an endpoint"
        outcome = HttpNodeBackend(
            base_url=endpoint, token=self.token, node_name=node.node_id, timeout=self.timeout
        ).execute(request)
        #: The receipt has to name a machine the node registry can resolve, so it records the declared
        #: id the work was sent to rather than the label the far side calls itself.
        return outcome.model_copy(update={"executed_on": node.node_id})

    def _candidates(self, request: ExecutionRequest) -> tuple[list[ExecutionNode], list[str]]:
        """The machines this attempt may go to, and why each of the others may not have it."""
        candidates: list[ExecutionNode] = []
        refusals: list[str] = []
        for node in self.nodes:
            reasons = node_allows_tier(node, self.privacy_tier)
            reasons.extend(node_fits(node, request.spec))
            reasons.extend(node_holds(node, request.cage))
            if node.endpoint is None:
                reasons.append("the node declares no endpoint, so there is nowhere to send the work")
            if reasons:
                #: The reasons travel with the refusal, because "no machine can run this" with no
                #: reason is a dead end - a reason is the next step.
                refusals.append(f"{node.node_id} ({node.label}): " + "; ".join(reasons))
            else:
                candidates.append(node)
        return candidates, refusals

    @staticmethod
    def _refuse(request: ExecutionRequest, code: str, message: str) -> ExecutionOutcome:
        """A placement the deployment cannot make: the run waits for a person, it does not run here."""
        return outcome_for_error(
            request,
            ToolResultStatus.FAILED,
            ApiError(code=code, error_class=ErrorClass.NEEDS_INPUT, message=message, field="node"),
        )
