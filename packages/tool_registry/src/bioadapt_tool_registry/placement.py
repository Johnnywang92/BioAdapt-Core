"""Where a tool can run (plan V2.0 sections 4.2, 4.4, 9.4, 9.6, 11.1).

This is the mirror of section 9.4: a tool declares what it needs, a node declares what it has, and a
run may only go where the second covers the first. Matching on the declaration instead of on a named
machine is what lets one node, a laptop plus a server, and a rack of nodes answer the same question -
and it is what keeps a strict-local project off a machine that can reach the internet, because such a
machine never enters the candidate set in the first place.

The reasons a node does not fit are returned, not just a verdict: "this machine cannot run that tool"
is an answer a person has to be able to read before they try again somewhere else.
"""

from __future__ import annotations

from typing import Iterable

from bioadapt_schemas.enums import PrivacyTier, ToolClass
from bioadapt_schemas.nodes import ExecutionNode, NodeNetworkMode, NodePosition

from .enums import NetworkMode
from .execution import ExecutionCage
from .spec import ToolSpec


def _host_allowed(host: str, allowed: Iterable[str]) -> bool:
    """Whether a declared host covers this one. A wildcard covers the subdomains under it."""
    for entry in allowed:
        if entry == host:
            return True
        if entry.startswith("*.") and host.endswith(entry[1:]):
            return True
    return False


def node_fits(node: ExecutionNode, spec: ToolSpec) -> list[str]:
    """The requirements this node does not meet. An empty list means the tool can run here."""
    reasons: list[str] = []
    resources = spec.resources
    caps = node.capabilities
    if resources.cpu > caps.cpu:
        reasons.append(f"the tool wants {resources.cpu} cpu, the node has {caps.cpu}")
    if resources.memory_mb > caps.memory_mb:
        reasons.append(f"the tool wants {resources.memory_mb}MB of memory, the node has {caps.memory_mb}MB")
    if resources.gpu > caps.gpu:
        reasons.append(f"the tool wants {resources.gpu} gpu, the node has {caps.gpu}")
    if spec.tool_class is ToolClass.PROJECT_ADHOC and not caps.can_run_containers:
        reasons.append("an ad-hoc tool runs in a container, and this node has no container runtime")
    reasons.extend(_network_reasons(node, spec))
    return reasons


def _network_reasons(node: ExecutionNode, spec: ToolSpec) -> list[str]:
    if spec.network.mode is NetworkMode.CLOSED:
        return []
    caps = node.capabilities
    if caps.network_mode is NodeNetworkMode.CLOSED:
        return ["the tool needs an outbound route, and this node has none"]
    if caps.network_mode is NodeNetworkMode.OPEN:
        return []
    missing = [host for host in spec.network.allowed_hosts if not _host_allowed(host, caps.allowed_hosts)]
    if missing:
        return [f"the tool needs to reach {host}, which this node does not allow" for host in missing]
    return []


def select_nodes(nodes: Iterable[ExecutionNode], spec: ToolSpec) -> list[ExecutionNode]:
    """The nodes this tool could run on."""
    return [node for node in nodes if not node_fits(node, spec)]


def node_allows_tier(node: ExecutionNode, tier: PrivacyTier) -> list[str]:
    """The reasons a project of this tier may not send its work to this machine (§4.2, §4.4).

    The tier is the promise a project made about where its bytes may go, so it is asked before the
    tool's own needs: a machine that cannot hold the promise must never be a candidate, and the tool
    fitting there does not make it one. Section 4.2's network declaration is the test - a closed
    machine keeps the bytes where the data lives; an open one can carry them anywhere.
    """
    reasons: list[str] = []
    network = node.capabilities.network_mode
    if tier is PrivacyTier.STRICT_LOCAL:
        if node.position is NodePosition.CLOUD:
            reasons.append("a strict-local project may not run on a machine declared in the cloud")
        if network is not NodeNetworkMode.CLOSED:
            reasons.append(
                "a strict-local project may only run where the network is closed, and this node's "
                f"network is {network}"
            )
    elif tier is PrivacyTier.HGR_CONTROLLED and network is not NodeNetworkMode.CLOSED:
        reasons.append(
            "a human-genetic-resource project may only run where the network is closed, and this "
            f"node's network is {network}"
        )
    return reasons


def node_holds(node: ExecutionNode, cage: ExecutionCage) -> list[str]:
    """The ceilings of one attempt this node cannot hold. An empty list means it can.

    ``node_fits`` asks whether the machine covers what the *tool* needs; this asks whether it covers
    what the *plan* approved for this attempt. The two differ whenever a plan budgets more than the
    tool's floor, and a machine that covers only the floor would take the work and then refuse it at
    the door - so the question is asked here, before anything is sent anywhere.
    """
    caps = node.capabilities
    reasons: list[str] = []
    if cage.cpu > caps.cpu:
        reasons.append(f"the attempt is allowed {cage.cpu} cpu, the node has {caps.cpu}")
    if cage.memory_mb > caps.memory_mb:
        reasons.append(
            f"the attempt is allowed {cage.memory_mb}MB of memory, the node has {caps.memory_mb}MB"
        )
    if cage.allow_gpu and caps.gpu < 1:
        reasons.append("the attempt is allowed a GPU, and this node has none")
    return reasons
